# SuperTrend reversal confirmation

`trade_filter.reversal_confirmation` задерживает только изменение уже открытой
позиции после противоположного raw SuperTrend edge. Первичный и повторный вход
из `flat` выполняется по прежнему open-to-open правилу.

## Конфигурация

```yaml
trade_mode: revers  # также: both, long, short
trade_filter:
  enabled: true
  zigzag:
    enabled: false
  volume:
    enabled: false
  time_filter:
    enabled: false
  reversal_confirmation:
    enabled: true
    confirm_bars: 2
```

`both` эквивалентен `revers`. В `long` подтверждённый противоположный edge
закрывает long в `flat`, но не открывает short; в `short` действует зеркальное
правило.

При edge на close бара `t0` сам event bar не считается подтверждающим.
Подтверждение требует raw trend на барах `t0+1 ... t0+N`; решение принимается
на close `t0+N`, а позиция изменяется на open `t0+N+1`.

## Совместно с time filter

```yaml
trade_filter:
  enabled: true
  zigzag:
    enabled: false
  volume:
    enabled: false
  time_filter:
    enabled: true
    window: "10:00-18:00"
  reversal_confirmation:
    enabled: true
    confirm_bars: 2
```

В standalone режиме pending может пересекать локальную полночь и sparse gaps.
В combined режиме decision, confirmation и execution должны принадлежать
одному `(local_calendar_date, window_id)`. Time reset отменяет pending и
планирует единственное закрытие на следующем open.

Не поддерживаются сочетания с ZigZag, volume, wakeup, position freeze,
equal-blocks, optimizer, walk-forward, early exit и execution model, отличный
от `open_to_open`.

## Excel и идентичность run

Legacy `Signals` сохраняет schema version 1 и семантику `raw_st_analytics`;
это источник false-start analytics, а не журнал фактических сделок.
`ExecutionSignals` содержит фактические переходы positions; при reversal close
идёт перед open на том же баре. `ReversalConfirmationEvents` содержит одну
финальную строку на attempt.

Active workbook имеет `export_schema_version=2` и
`tester_run_fingerprint` (SHA-256 нормализованных входных данных, параметров
стратегии, execution model, effective filter snapshot и period splits).
Absent/disabled конфигурация не получает active-only sheets или metadata.
