# План апгрейда `time_filter`: несколько окон и независимый режим time-only

## 1. Цель

Реализовать два связанных изменения:

1. Поддержать несколько разрешённых временных интервалов через `windows`.
2. Сделать `time_filter` самостоятельным торговым подфильтром, который работает без ZigZag и Volume.

Фильтр должен одинаково работать в:

- WF-оптимизации через `config.yaml`;
- одиночном tester;
- batch tester через `config_tester.yaml`;
- комбинированных режимах с ZigZag и Volume;
- самостоятельном режиме time-only.

`time_filter` не должен:

- обрезать исходный DataFrame;
- удалять бары;
- сокращать train/test/OOS-интервалы;
- менять границы WF-срезов;
- исключать внеоконные бары из расчёта SuperTrend, ATR, метрик или временной шкалы.

Прогон выполняется по всем барам. Фильтр управляет только разрешением позиций, входов и reset-событиями.

---

## 2. Целевой контракт активации

Корневой переключатель сохраняется:

```yaml
trade_filter:
  enabled: true
```

Подфильтр считается активным только при включённом корне.

Целевая формула:

```text
zigzag_active =
    trade_filter.enabled
    AND zigzag.enabled

volume_active =
    trade_filter.enabled
    AND volume.enabled

time_filter_active =
    trade_filter.enabled
    AND time_filter.enabled

trade_filter_active =
    zigzag_active
    OR volume_active
    OR time_filter_active
```

Таким образом, следующая конфигурация становится допустимой:

```yaml
trade_filter:
  enabled: true

  zigzag:
    enabled: false

  time_filter:
    enabled: true
    window: "09:00-19:00"

  volume:
    enabled: false
```

Если `trade_filter.enabled: true`, но ZigZag, Volume и Time Filter одновременно выключены, конфигурация должна отклоняться с fail-fast ошибкой:

```text
at least one trade subfilter must be enabled when trade_filter.enabled=true:
zigzag, volume, or time_filter
```

При `trade_filter.enabled: false` весь блок остаётся полным no-op независимо от значений вложенных переключателей.

---

## 3. Поддерживаемые комбинации

После изменений должны работать все комбинации:

| ZigZag | Volume | Time | Режим |
|---:|---:|---:|---|
| 0 | 0 | 0 | Ошибка при `trade_filter.enabled=true` |
| 0 | 0 | 1 | `time_only` |
| 0 | 1 | 0 | `volume_only` |
| 0 | 1 | 1 | `volume_and_time` |
| 1 | 0 | 0 | `zigzag_only` |
| 1 | 0 | 1 | `zigzag_and_time` |
| 1 | 1 | 0 | `zigzag_and_volume` |
| 1 | 1 | 1 | `zigzag_volume_and_time` |

При `trade_filter.enabled=false` всегда используется существующий baseline-путь независимо от таблицы.

---

## 4. Формат конфигурации временных окон

### 4.1 Новый формат

```yaml
time_filter:
  enabled: true
  windows:
    - "09:00-12:00"
    - "16:00-18:00"
```

### 4.2 Старый формат

Сохраняется без изменений:

```yaml
time_filter:
  enabled: true
  window: "09:00-19:00"
```

### 4.3 Взаимоисключение

Одновременно указывать `window` и `windows` запрещено:

```yaml
time_filter:
  enabled: true
  window: "09:00-19:00"
  windows:
    - "09:00-12:00"
```

Такой конфиг должен быть отклонён независимо от значения `enabled`.

### 4.4 Обязательность

При:

```yaml
trade_filter:
  enabled: true
  time_filter:
    enabled: true
```

должен быть указан ровно один из ключей:

```text
window
windows
```

При `time_filter.enabled: false` оба поля могут отсутствовать.

---

## 5. Семантика интервалов

Все интервалы являются полуоткрытыми:

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

На плотных минутных данных:

- `09:00–11:59` — позиции разрешены;
- `12:00–15:59` — позиции запрещены;
- `16:00–17:59` — позиции разрешены;
- с `18:00` — позиции запрещены.

Секунды и наносекунды игнорируются:

```text
11:59:59 → минута 11:59 → внутри
12:00:30 → минута 12:00 → снаружи
```

Временные компоненты берутся из индекса данных. Автоматического перевода UTC, local time или DST-коррекции нет.

---

## 6. Валидация `windows`

`windows` должен быть непустым YAML-списком строк.

Для каждого элемента проверяется:

- тип `str`;
- формат строго `HH:MM-HH:MM`;
- часы находятся в диапазоне `00–23`;
- минуты находятся в диапазоне `00–59`;
- начало строго раньше конца;
- интервалы через полночь запрещены.

После разбора интервалы автоматически сортируются по времени начала.

Запрещаются:

- дубли;
- частичные пересечения;
- полные вложения;
- соприкасающиеся интервалы.

Для соседних нормализованных интервалов должно выполняться:

```text
previous.end < next.start
```

Соприкасающиеся интервалы:

```text
[09:00,12:00)
[12:00,18:00)
```

запрещаются, потому что их объединение непрерывно и не создаёт обязательного reset между циклами.

Если нужен непрерывный диапазон, он задаётся одним интервалом:

```yaml
windows:
  - "09:00-18:00"
```

---

## 7. Поведение при выключенном фильтре

При `time_filter.enabled: false`:

```text
time_filter_in_window = all True
time_filter_reset_event = all False
```

Сохраняется существующая обратная совместимость: содержимое `window` или `windows` может храниться в выключенном блоке без глубокой проверки диапазонов.

При последующем включении блока выполняется полная валидация.

Структурные ошибки проверяются всегда:

- неизвестные ключи;
- неправильный тип `enabled`;
- одновременное присутствие `window` и `windows`.

---

## 8. Ошибки конфигурации

Старые error keys для `window` сохраняются.

Добавляются как минимум:

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

Сообщения должны содержать путь к проблемному полю и, для элемента списка, его индекс.

Пример:

```text
trade_filter.time_filter.windows[1] overlaps windows[0]
```

---

## 9. Каноническое внутреннее представление

`TradeFilterTimeFilterConfig` расширяется полем:

```python
windows: Optional[list[str]]
```

Resolver преобразует `window` или `windows` в единый отсортированный формат:

```python
_windows_minutes: tuple[tuple[int, int], ...]
```

Пример:

```text
"09:00-12:00" → (540, 720)
"16:00-18:00" → (960, 1080)
```

Результат:

```python
_windows_minutes = (
    (540, 720),
    (960, 1080),
)
```

Старый формат:

```yaml
window: "09:00-19:00"
```

нормализуется в:

```python
_windows_minutes = ((540, 1140),)
```

Runtime должен использовать только `_windows_minutes`.

Старые поля:

```text
_start_hour
_start_minute
_end_hour
_end_minute
```

можно временно заполнять для legacy test doubles и обратной совместимости, но они не должны оставаться основным runtime-контрактом.

---

## 10. Общая временная маска

Для каждого интервала:

```text
in_window_i[t] =
    start_i <= minute_of_day[t] < end_i
```

Общая маска:

```text
time_filter_in_window[t] =
    in_window_1[t]
    OR in_window_2[t]
    OR ...
```

Построение маски должно находиться в общем helper и использоваться всеми runtime-путями:

- time-only;
- volume-only с включённым временем;
- ZigZag/FSM с включённым временем.

Дублирование математической логики между этими режимами запрещено.

---

## 11. Reset-события на плотных данных

Основное правило:

```text
reset_event[t] =
    in_window[t-1]
    AND NOT in_window[t]
```

Для минутных окон:

```text
[09:00,12:00)
[16:00,18:00)
```

получаем reset на барах:

```text
12:00
18:00
```

Дополнительные reset-события на последующих внеоконных барах не создаются.

На первом баре ряда:

```text
reset_event[0] = False
```

Каждый backtest или WF-срез начинает выполнение с чистого runtime-состояния и без унаследованной позиции.

---

## 12. Защита от временных пропусков

Простого сравнения объединённой маски недостаточно.

Например:

```text
11:59 → 16:00
```

даёт:

```text
True → True
```

хотя конец утреннего окна был пересечён.

Общий helper должен дополнительно обнаруживать пересечение конца интервала между соседними timestamp.

Если предыдущий наблюдаемый бар находился внутри разрешённого интервала, а следующий timestamp находится после его окончания, reset материализуется на первом следующем существующем баре.

Пример:

```text
11:59 → 16:00
```

Результат:

```text
time_filter_in_window[16:00] = 1
time_filter_reset_event[16:00] = 1
```

Reset имеет приоритет над входом. Поэтому на `16:00` новый вход не выполняется; новое торговое решение возможно со следующего бара.

Аналогично:

```text
17:59 текущего дня → 09:00 следующего дня
```

создаёт reset на `09:00` следующего дня, если между timestamp отсутствовали внеоконные строки.

Это fail-closed поведение, исключающее перенос позиции и состояния между торговыми циклами.

---

## 13. Open-to-open исполнение

Сохраняется текущая модель:

```text
решение на close(t)
исполнение на open(t+1)
```

Если reset возникает на баре `12:00`:

- на этом баре формируется reset и сигнал закрытия;
- FSM/lifecycle/time state сбрасываются;
- `positions[t+1] = 0`;
- фактическое нулевое состояние возникает на следующем баре;
- сделка получает `exit_reason="filter_time_reset"`.

Фраза «закрытие в 12:00» означает сигнал закрытия на баре `12:00` в рамках существующей модели, а не исполнение на open `12:00`.

Общую модель исполнения менять нельзя.

---

## 14. Поведение time-only

### 14.1 Источник базовых позиций

В режиме time-only сначала формируются стандартные SuperTrend-позиции существующим baseline-механизмом:

```python
raw_positions = generate_positions(...)
```

После этого применяется временной lifecycle overlay.

Все исходные бары продолжают участвовать в:

- расчёте SuperTrend;
- ATR;
- raw positions;
- доходностях временной шкалы;
- WF-разбиении;
- диагностике.

### 14.2 Вне временного окна

Вне разрешённого времени:

- итоговая позиция должна быть `0`;
- новая позиция не открывается;
- reversal не открывает противоположную позицию;
- reset закрывает существующую позицию по open-to-open правилам.

### 14.3 Внутри временного окна

Внутри окна time-only следует текущему целевому состоянию baseline SuperTrend.

После начала нового окна фильтр может снова открыть позицию в направлении текущего baseline SuperTrend без обязательного ожидания нового ST flip.

С учётом open-to-open:

- решение принимается на первом разрешённом баре;
- позиция появляется на следующем баре.

Это правило должно быть явно протестировано. Иначе time-only может оставаться без позиции до следующего разворота SuperTrend, что не соответствует смыслу временной маски baseline-стратегии.

### 14.4 Reset-бар внутри нового окна после пропуска

Если из-за пропущенных данных один бар одновременно имеет:

```text
time_filter_in_window = 1
time_filter_reset_event = 1
```

reset имеет приоритет:

- старая позиция закрывается;
- новый вход на этом баре запрещён;
- вход возможен со следующего решения.

---

## 15. Архитектура runtime-dispatch

Текущий dispatch:

```text
zigzag
else volume
else baseline
```

нужно заменить на:

```text
if zigzag_active:
    run ZigZag/FSM path
elif volume_active:
    run standalone-volume path
elif time_filter_active:
    run time-only path
else:
    run baseline path
```

При включённом ZigZag он остаётся главным lifecycle runtime и получает Volume и Time как дополнительные условия.

При выключенном ZigZag и включённом Volume используется standalone-volume runtime, который также применяет Time.

При выключенных ZigZag и Volume, но включённом Time используется новый time-only runtime.

Рекомендуется выделить time-only реализацию в отдельный модуль, например:

```text
supertrend_optimizer/core/time_only_filter.py
```

При этом вычисление временной маски и reset-событий должно оставаться в общем `_reset_events.py`.

---

## 16. Общие helper-функции активации

Добавить:

```python
is_time_filter_enabled(tf)
```

Функция должна возвращать `True`, только если:

```text
trade_filter.enabled is True
AND
trade_filter.time_filter.enabled is True
```

Добавить общий helper:

```python
is_any_trade_subfilter_enabled(tf)
```

или эквивалент:

```text
is_zigzag_enabled(tf)
OR is_volume_enabled(tf)
OR is_time_filter_enabled(tf)
```

Все места, где сейчас используется:

```text
zigzag_enabled or volume_enabled
```

должны быть проаудированы и обновлены.

Это касается как минимум:

- backtest dispatch;
- tester runner;
- batch tester;
- WF orchestrator;
- diagnostics summary;
- filter snapshot;
- export activation;
- validation данных;
- integration wrappers.

---

## 17. Устранение дублирования reset-логики

В ZigZag runtime уже существует `_resolve_apply_reset_events`, но после него часть daily/time reset вычисляется повторно.

В рамках реализации необходимо:

- оставить один источник reset-масок;
- использовать результат `_resolve_apply_reset_events`;
- удалить повторный расчёт из основного тела `apply`;
- не создавать отдельную копию логики для time-only;
- не создавать отдельную копию логики для tester.

Общий helper должен возвращать:

```text
time_filter_enabled
time_filter_in_window
time_filter_reset_event
daily_reset_event
combined_reset_event
```

---

## 18. Взаимодействие с `daily_reset`

Общая маска:

```text
combined_reset_event =
    daily_reset_event
    OR time_filter_reset_event
```

Приоритет:

```text
daily_reset > time_filter_reset
```

В чистом time-only режиме ZigZag и Volume daily-reset могут отсутствовать. В таком случае:

```text
daily_reset_event = all False
```

Если позднее появится общий независимый daily-reset, это должно рассматриваться как отдельное изменение.

---

## 19. Диагностика time-only

Time-only должен выдавать как минимум:

```text
trade_filter_enabled
time_filter_enabled
time_filter_in_window
time_filter_reset_event
filter_allowed_entry
filter_block_reason
daily_reset_event
```

FSM- и Volume-специфичные поля не должны искусственно заполняться фиктивными значениями, если экспортный контракт допускает их отсутствие.

Если существующий exporter требует фиксированный набор колонок, он должен быть адаптирован так, чтобы:

- общие колонки присутствовали всегда на активном trade-filter пути;
- ZigZag-колонки появлялись только при ZigZag;
- Volume-колонки появлялись только при Volume;
- Time-колонки появлялись при Time;
- отсутствие неприменимых колонок не приводило к ошибке.

Для summary добавить:

```text
mode = "time_only"
```

и сохранить счётчики:

```text
time_filter_enabled
time_filter_reset_count
time_filter_bars_in_window
time_filter_bars_out_window
```

Дополнительно желательно считать:

```text
time_filter_blocked_entry_count
time_filter_forced_exit_count
```

---

## 20. Причины блокировки и выхода

На reset-баре:

```text
filter_block_reason = "time_filter_reset"
```

Для конкретного baseline-сигнала, заблокированного вне окна:

```text
filter_block_reason = "time_filter_out_of_window"
```

На пассивном внеоконном баре без нового торгового решения причина блокировки не должна искусственно создаваться, если текущий диагностический контракт считает только реальные заблокированные решения.

Trade-level причина принудительного выхода:

```text
exit_reason = "filter_time_reset"
```

При совпадении с daily reset:

```text
filter_block_reason = "daily_reset"
exit_reason = "filter_daily_reset"
```

---

## 21. Snapshot и сериализация конфигурации

Конфигурационный snapshot должен включать:

```text
trade_filter.enabled
zigzag.enabled
volume.enabled
time_filter.enabled
time_filter.window
time_filter.windows
effective mode
```

Внутренние resolver-поля вроде `_windows_minutes` не должны попадать в пользовательский YAML, но могут присутствовать во внутреннем debug snapshot, если это уже принято в проекте.

Порядок входного `windows` не должен менять:

- runtime-результат;
- effective fingerprint;
- cache key;
- сравнение эквивалентных конфигураций.

Для fingerprint следует использовать канонически отсортированные интервалы.

---

## 22. Тесты конфигурации

Для WF loader и tester loader проверить:

- старый `window`;
- новый `windows`;
- автоматическую сортировку;
- конфликт `window + windows`;
- отсутствие обоих полей при включённом Time;
- пустой список;
- scalar вместо списка;
- mapping вместо списка;
- `null`;
- нестроковый элемент;
- неверный формат;
- неверные часы;
- неверные минуты;
- нулевую длину;
- cross-midnight;
- дубли;
- частичное пересечение;
- вложение;
- соприкасающиеся интервалы;
- неизвестные ключи;
- disabled-path.

Отдельно проверить activation matrix из раздела 3.

---

## 23. Тесты общей временной маски

На плотном индексе проверить:

- `08:59` — вне окна;
- `09:00` — внутри;
- `11:59` — внутри;
- `12:00` — вне окна и reset;
- `12:01–15:59` — вне окна без повторных reset;
- `16:00` — внутри;
- `17:59` — внутри;
- `18:00` — вне окна и reset;
- после `18:00` — вне окна без повторных reset.

Дополнительно:

- секунды игнорируются;
- tz-naive индекс;
- tz-aware индекс;
- non-DatetimeIndex;
- non-monotonic индекс;
- несовпадение длины;
- пустой индекс;
- disabled filter.

---

## 24. Тесты пропусков

Проверить:

```text
11:59 → 12:01
```

Reset возникает на `12:01`.

Проверить:

```text
11:59 → 16:00
```

На `16:00`:

```text
in_window = 1
reset_event = 1
```

Новый вход блокируется до следующего решения.

Проверить:

```text
17:59 → 09:00 следующего дня
```

Reset возникает на первом баре нового дня.

Проверить отсутствие синтетического reset на первом баре отдельного backtest/WF-среза.

---

## 25. Тесты time-only

В режиме:

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

проверить:

- конфиг принимается;
- Volume runtime не строится;
- ZigZag global stats не строятся;
- исходный DataFrame не обрезается;
- число баров результата совпадает с числом входных баров;
- WF-границы не меняются;
- raw SuperTrend рассчитывается на всех барах;
- вне окон итоговая позиция равна нулю;
- внутри окон baseline-позиция разрешена;
- после начала окна возможно восстановление текущего направления без нового ST flip;
- reset закрывает позицию по `t → t+1`;
- reversal вне окна не открывает позицию;
- reset очищает time-only lifecycle;
- диагностика присутствует;
- `filter_time_reset` прикрепляется к сделке;
- Volume- и ZigZag-диагностика не подделывается.

---

## 26. Тесты комбинированных режимов

Проверить отдельно:

### Time + Volume

- Volume решает, допустим ли вход по объёму;
- Time дополнительно запрещает вход вне окна;
- reset Time закрывает позицию независимо от Volume;
- при одновременных причинах применяется документированный приоритет.

### Time + ZigZag

- ZigZag управляет FSM;
- Time запрещает lifecycle-входы вне окна;
- Time reset полностью очищает FSM и candidate state;
- новое окно начинает новый цикл.

### Time + ZigZag + Volume

- все три условия применяются;
- нет двойного построения временной маски;
- reset выполняется один раз;
- diagnostics и summary не удваивают события.

### Режимы без Time

Существующие ZigZag-only и Volume-only результаты должны остаться побитово совместимыми.

---

## 27. Паритет WF, single tester и batch tester

На одном небольшом детерминированном dataset выполнить:

- прямой backtest;
- WF runtime;
- single tester;
- batch tester.

Сравнить:

```text
raw positions
final positions
time_filter_in_window
time_filter_reset_event
filter_block_reason
entry/exit indexes
exit_reason
summary counters
```

При одинаковых параметрах результаты должны совпадать.

---

## 28. Регрессионные гарантии

Нужно доказать:

1. При `trade_filter.enabled=false` результат побитово совпадает с baseline.
2. Старый `window` совпадает с эквивалентным `windows` из одного элемента.
3. ZigZag-only не меняется.
4. Volume-only не меняется.
5. Существующие diagnostics/export не ломаются.
6. Golden snapshots либо остаются неизменными, либо меняются только там, где активирован новый режим.
7. Порядок `windows` не влияет на результат.
8. Time-only не требует столбец Volume.
9. Time-only не строит ZigZag global stats.
10. Внеоконные бары остаются в данных и метриках.

---

## 29. Обновление рабочих конфигов

Если оба рабочих конфига должны использовать чистый time-only, целевой блок:

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
```

Перед изменением необходимо проверить зависимые блоки.

Если `wakeup_regime` требует ZigZag/Mode D, его нельзя оставлять включённым при `zigzag.enabled: false`. Для чистого time-only нужно:

- установить `wakeup_regime.enabled: false`;
- либо удалить этот активный блок;
- оставить его параметры только как неактивный шаблон, если валидатор это допускает.

Аналогично нужно проверить ZigZag-specific:

- `triggers`;
- `lifecycle`;
- `diagnostics`;
- Mode D;
- candidate settings.

Они не должны случайно активировать или инвалидировать time-only конфигурацию.

---

## 30. Документация

Обновить `docs/time_filter_spec.md`, добавив:

- независимый режим time-only;
- activation matrix;
- новый `windows`;
- legacy `window`;
- полуоткрытые интервалы;
- auto-sort;
- запрет пересечений и соприкосновений;
- поведение при пропусках;
- open-to-open закрытие;
- правило первого бара;
- восстановление baseline-позиции в новом окне;
- отсутствие обрезки данных;
- diagnostic contract;
- приоритеты reset;
- комбинированные режимы.

Также обновить примеры `config.yaml` и `config_tester.yaml`.

---

## 31. Порядок реализации

1. Зафиксировать baseline и characterization текущих режимов.
2. Добавить activation helpers и новую activation matrix.
3. Расширить dataclass и строгие схемы ключом `windows`.
4. Реализовать валидацию и канонический resolver.
5. Расширить общий helper временных событий.
6. Устранить дублирование reset-логики в ZigZag runtime.
7. Реализовать time-only runtime.
8. Обновить backtest dispatch.
9. Обновить WF, single tester и batch tester.
10. Обновить diagnostics, summary, snapshots и exporters.
11. Добавить unit- и integration-тесты.
12. Запустить полный regression suite.
13. Обновить документацию.
14. Только после успешных тестов изменить рабочие конфиги.

---

## 32. Критерии готовности

Работа считается завершённой, если одновременно выполняется следующее:

- `time_filter` работает при выключенных ZigZag и Volume;
- старый `window` продолжает работать;
- новый `windows` работает;
- позиции вне разрешённого времени отсутствуют;
- данные и WF-периоды не обрезаются;
- на каждом окончании окна возникает reset;
- позиции закрываются по существующей open-to-open модели;
- временные пропуски обрабатываются fail-closed;
- все комбинации подфильтров имеют определённое поведение;
- WF, single tester и batch tester дают одинаковый результат;
- disabled baseline не изменился;
- старые ZigZag-only и Volume-only режимы не регрессировали;
- оба рабочих конфига успешно проходят загрузку и короткий smoke-run.
