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

  diagnostics:
    export_state_columns: true
    export_trigger_columns: true
```

Все ZigZag thresholds задаются как доли цены, не как проценты: `0.005` = 0.5%.
`candidate_trigger_threshold: auto` берет threshold из full-dataset
распределения ZigZag leg heights по `candidate_trigger_quantile`.

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

Trade-level `exit_reason` добавляет:

- `st_flip` - обычный SuperTrend exit/reversal.
- `filter_stopping_opposite_flip` - выход из позиции в `ST_STOPPING` по
  ближайшему противоположному ST flip.
- `pending_open_trade_at_end` - открытая сделка помечена на последнем баре.

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
