# ZigZag ST trade filter

Этот документ - короткая пользовательская памятка по Phase 2 ZigZag
SuperTrend trade filter для Tester. Он не заменяет спецификацию: source of
truth остается [zigzag_st_trade_filter_spec_v1_1.txt](zigzag_st_trade_filter_spec_v1_1.txt).

## Что делает фильтр

Фильтр сужает входы обычной SuperTrend-стратегии через close-only ZigZag
режим. Он не добавляет новых направлений и не расширяет `trade_mode`: если
`trade_mode: long`, short-входы по-прежнему запрещены; если `short`, запрещены
long-входы; `revers` / `both` разрешают оба направления.

Логика работает внутри backtest path до расчета returns, equity, trades и
metrics. Это не пост-фильтрация Excel-таблицы сделок. Disabled-path
baseline-safe: если блока `trade_filter` нет или он закомментирован /
`enabled: false`, поведение должно совпадать с обычным Tester.

В Phase 2 v0.4 включенный фильтр поддержан только для legacy segmentation.
Комбинация `segmentation.mode: equal_blocks` + `trade_filter.enabled: true`
отклоняется на этапе config validation.

## FSM: 5 состояний

Фильтр хранит состояние на каждом баре:

- `OFF` - фильтр выключен для новых входов; ST-входы блокируются.
- `WAIT_FIRST_ST_FLIP` - ZigZag trigger уже сработал, фильтр ждет первый ST
  flip, разрешенный текущим `trade_mode`.
- `ST_ACTIVE_FREEZE` - ST-входы/выходы разрешены, median-stop еще не проверяет
  отключение режима.
- `ST_ACTIVE_MONITORING` - ST-логика разрешена, но на confirm-bar проверяется
  `local_median_N >= global_median`; при fail-closed начинается stopping.
- `ST_STOPPING` - новые входы запрещены; открытая позиция закрывается только
  ближайшим противоположным ST flip, после чего состояние возвращается в `OFF`.

## YAML пример

По умолчанию оставляйте блок закомментированным или `enabled: false`.
Чтобы включить фильтр, переключите segmentation в `legacy`, раскомментируйте
блок и поставьте `enabled: true`.

```yaml
segmentation:
  mode: legacy
  n_parts: 7

trade_filter:
  enabled: true
  type: zigzag_st_mode

  zigzag:
    global_stats_source: full_dataset
    leg_height_mode: pct
    reversal_threshold: 0.005
    candidate_trigger_threshold: auto
    candidate_trigger_quantile: 0.80
    global_median: auto
    local_window: 5

  triggers:
    candidate_threshold:
      enabled: true
    confirmed_median:
      enabled: true

  lifecycle:
    freeze_confirmed_legs: 5
    stop_check: confirm_bar_only
    stopping_exit: opposite_st_flip
    # exit_off_mode: "exit B"          # раскомментировать для exit B
    # exit_off_zz_leg_count: 3         # обязателен при exit B
    # exit_b_immediate_off: false      # true — немедленное OFF без ST_STOPPING

  diagnostics:
    export_state_columns: true
    export_trigger_columns: true
```

Все ZigZag thresholds задаются как доли цены, не как проценты: `0.005` = 0.5%.
`candidate_trigger_threshold: auto` берет threshold из full-dataset
распределения ZigZag leg heights по `candidate_trigger_quantile`.

## Mode D wakeup ATR

`zigzag_st_filter.apply()` may receive `high` and `low` runtime arrays.
This does not make ZigZag OHLC-based: ZigZag pivots, leg heights,
`candidate_height_pct`, and `local_median_N` remain derived from `close` or
precomputed `per_bar` data.

When Mode D wakeup `entry.atr_expansion.enabled` is true, the wakeup ATR ratio
uses real OHLC True Range from `high`, `low`, and `close`. In that path direct
`apply()` calls require finite 1-D OHLC arrays of matching length with
`high >= low`.

## Excel export

Когда фильтр выключен, новые filter-колонки и filter-листы не создаются.

Когда фильтр включен, legacy workbook получает дополнительные trade-level
колонки:

- `Entry Filter State`
- `Entry Trigger Source`
- `Exit Reason`

Signals sheet получает 4 колонки около ST-сигнала:

- `Filter State at Signal`
- `Filter Decision`
- `Filter Block Reason`
- `Filter Trigger Source`

Дополнительные листы:

- `FilterDiagnostics_100` - bar-level diagnostics для 100% legacy period.
- `ZigZag_Trigger_Events` - реконструкция ZigZag trigger events.
- `filters_summary` - параметры фильтра, thresholds, counters и bars-in-state
  по legacy periods.

Лист `false start` при включенном `export_state_columns` добавляет колонку
`Filter Block Reason at Signal`, чтобы отличать pre-filter false start от
разрешенной фильтром сделки.

## Diagnostics flags

```yaml
trade_filter:
  diagnostics:
    export_state_columns: true
    export_trigger_columns: true
```

`export_state_columns` включает state-oriented export: trade/signal filter
колонки, `FilterDiagnostics_100`, `filters_summary` и filter reason на
`false start`.

`export_trigger_columns` включает `ZigZag_Trigger_Events`. Если нужны только
итоговые метрики и сделки без подробной trigger-реконструкции, можно поставить
его в `false`.

## Migration guide

1. Откройте существующий `config_tester.yaml`.
2. Убедитесь, что baseline запуск проходит с отсутствующим или выключенным
   `trade_filter`.
3. Переключите `segmentation.mode` на `legacy`.
4. Вставьте или раскомментируйте блок `trade_filter` из шаблона.
5. Поставьте `enabled: true`.
6. Начните с консервативных значений: `reversal_threshold: 0.005`,
   `candidate_trigger_threshold: auto`, `candidate_trigger_quantile: 0.80`,
   `local_window: 5`, `freeze_confirmed_legs: 5`.
7. Сравните `result_legacy.xlsx` до/после: количество сделок может снизиться.
   Если сделок стало меньше `min_trades_required`, ratio metrics получают
   обычное tester invalid-value поведение; фильтр это правило не смягчает.

Rollback простой: закомментируйте весь блок `trade_filter` или поставьте
`enabled: false`. Default остается baseline-safe.

## FILTER_REASON_WHITELIST

Bar-level `filter_block_reason` использует стабильный whitelist:

- `none` - вход не заблокирован фильтром на этом баре.
- `filter_off` - ST flip пришел, пока FSM в `OFF`.
- `waiting_for_allowed_st_flip` - ожидание первого разрешенного ST flip после
  trigger; это whitelist reason, но passive WAIT-бары могут не эмитить его как
  concrete blocked decision.
- `trade_mode_disallowed_flip` - flip не разрешен текущим `trade_mode`.
- `local_median_unavailable` - в monitoring на confirm-bar local median
  недоступна или невалидна; применяется fail-closed.
- `invalid_stats` - runtime stats невалидны.
- `stopping_mode_no_new_entries` - FSM в `ST_STOPPING`, новые входы запрещены.
- `insufficient_global_stats` - глобальной ZigZag статистики недостаточно.
- `daily_reset` - бар совпадает с событием ежедневного сброса (`daily_reset_event == 1`).
- `time_filter_reset` - бар является первым баром вне торгового окна `time_filter` (приоритет ниже `daily_reset`).

Trade-level `exit_reason` добавляет (приоритет сверху вниз):

1. `filter_daily_reset` — bar-level `daily_reset_event == 1`.
2. `filter_time_reset` — bar-level `time_filter_reset_event == 1` (при `time_filter.enabled=true`); только если `daily_reset_event == 0`.
3. `pending_open_trade_at_end` — позиция ещё открыта на последнем баре отрезка.
4. `filter_exit_b_immediate_off` — lifecycle завершился через immediate-off path
   (`exit_b_immediate_off_triggered[exit_signal_idx] == 1`).
5. `filter_stopping_opposite_flip` — выход из позиции в `ST_STOPPING` по
   ближайшему противоположному ST flip (legacy path).
6. `st_flip` — обычный SuperTrend exit/reversal.

Приоритет #2 выше #3: если immediate-off сработал на последнем или
предпоследнем баре отрезка (t == n-1 или t == n-2 при `exit_index == n-1`),
`exit_reason` будет `pending_open_trade_at_end`. Факт срабатывания
immediate-off подтверждается через `exit_b_immediate_off_triggered[t] == 1`
в diagnostics-массиве.

## exit_b_immediate_off

Опциональный режим для `exit_off_mode: "exit B"`. При
`exit_b_immediate_off: true` lifecycle завершается **немедленно как `OFF`**
на том же баре `t`, где `zz_legs_since_lifecycle_start` достигает
`exit_off_zz_leg_count`. Фаза `ST_STOPPING` для этого события не
используется.

### Применимость

`exit_b_immediate_off` допустим **только** при `exit_off_mode: "exit B"` и
`trade_filter.enabled: true`. Присутствие ключа в YAML при других условиях
отклоняется валидатором с соответствующим `error_key`.

### Поведение на пороговом баре t

| Что | Значение |
|-----|----------|
| `state_arr[t]` | `"OFF"` |
| `positions[t]` | текущая позиция (не look-ahead) |
| `positions[t+1]` | `0` — закрытие на `open(t+1)` по контракту open-to-open |
| `zz_leg_stop_triggered[t]` | `1` (legacy-инвариант, срабатывает в **обоих** режимах) |
| `exit_b_immediate_off_triggered[t]` | `1` |
| `exit_b_immediate_off_config[:]` | `1` broadcast по всему отрезку |
| `confirmed_legs_since_start[t]` | `-1` (OFF sentinel) |
| `zz_legs_since_lifecycle_start[t]` | `-1` (OFF sentinel) |

Отличие от legacy path: в legacy `state_arr[t] == "ST_STOPPING"`, что
позволяет ещё одному ST flip завершить позицию; в immediate-off lifecycle
уже `OFF` на том же баре.

### filter_block_reason на пороговом баре

`filter_block_reason` не имеет специальной ветки для immediate-off и
следует стандартным правилам:

| Условие на баре t | `filter_block_reason[t]` |
|---|---|
| `daily_reset_event[t] == 1` | `"daily_reset"` |
| `time_filter_reset_event[t] == 1` (и `daily_reset == 0`) | `"time_filter_reset"` |
| `flip_dir == 0` (нет ST flip) | `"none"` |
| `flip_dir != 0` (state уже OFF) | `"filter_off"` |

> **Предупреждение**: значение `"filter_off"` на immediate-off баре с
> `flip_dir != 0` означает **не** «lifecycle был OFF до этого flip», а
> «lifecycle завершился immediate-off в рамках той же итерации, и flip
> пришёл уже после». Различить эти случаи можно только через
> `exit_b_immediate_off_triggered[t] == 1`. Аналитический код,
> агрегирующий `"filter_off"` без учёта этого флага, будет некорректно
> включать immediate-off бары в категорию «lifecycle was idle».

### Приоритет relative to daily_reset

`daily_reset` имеет приоритет над immediate-off: если `daily_reset_event[t] == 1`,
wipe выполняется до проверки порога exit B, поэтому ни
`exit_b_immediate_off_triggered[t]`, ни `zz_leg_stop_triggered[t]` не
выставляются в `1` на reset-баре.

### Новые per-bar diagnostics (always-present)

Оба ключа присутствуют в `filter_diagnostics` **всегда** — и при
`exit_b_immediate_off: true`, и при `false` (в режиме `false` заполнены
нулями):

| Ключ | dtype | Семантика |
|------|-------|-----------|
| `exit_b_immediate_off_triggered` | `np.int8` | `1` только на баре `t` immediate-off; иначе `0` |
| `exit_b_immediate_off_config` | `np.int8` | Broadcast: `1` если флаг включён, `0` если нет |

### Excel export

В листе `FilterDiagnostics_100`:

- `"Exit-B Immediate OFF Triggered"` — колонка `exit_b_immediate_off_triggered`
- `"Exit-B Immediate OFF Config"` — колонка `exit_b_immediate_off_config`

В листе `filters_summary.params` строка:

- `"Exit-B Immediate OFF"` — значение `True` или `False` (всегда присутствует,
  не «—»).

### YAML-пример

```yaml
trade_filter:
  enabled: true
  type: zigzag_st_mode

  zigzag:
    global_stats_source: full_dataset
    leg_height_mode: pct
    reversal_threshold: 0.005
    candidate_trigger_threshold: auto
    candidate_trigger_quantile: 0.80
    global_median: auto
    local_window: 5

  triggers:
    candidate_threshold:
      enabled: true
    confirmed_median:
      enabled: false   # в exit B режиме median-stop не задействован

  lifecycle:
    freeze_confirmed_legs: 0          # exit B: счёт ног начинается сразу
    stop_check: confirm_bar_only
    stopping_exit: opposite_st_flip
    exit_off_mode: "exit B"
    exit_off_zz_leg_count: 3          # после 3 подтверждённых ног → OFF
    exit_b_immediate_off: true        # немедленное OFF без фазы ST_STOPPING

  diagnostics:
    export_state_columns: true
    export_trigger_columns: true
```

Вариант с `exit_b_immediate_off: false` (поведение по умолчанию — legacy path
через `ST_STOPPING`):

```yaml
  lifecycle:
    freeze_confirmed_legs: 0
    stop_check: confirm_bar_only
    stopping_exit: opposite_st_flip
    exit_off_mode: "exit B"
    exit_off_zz_leg_count: 3
    # exit_b_immediate_off отсутствует или:
    # exit_b_immediate_off: false    # явный false, равнозначен отсутствию ключа
```

## Calibration

Перед legacy run включенный фильтр один раз строит full-dataset статистику:
`build_zigzag_global_stats(close, trade_filter_config)`.

Эта стадия:

- детектирует close-only ZigZag confirmed legs по `reversal_threshold`;
- считает `global_median` по высотам confirmed legs;
- материализует `candidate_trigger_threshold` как explicit numeric threshold
  или как quantile при `candidate_trigger_threshold: auto`;
- fail-fast останавливает запуск до backtest, если статистику нельзя
  корректно построить.

Статистика строится один раз на полном validated CSV и переиспользуется всеми
legacy periods. Slice-local `local_median_N` остается causal и не подглядывает
в будущие бары периода.
