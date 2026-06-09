# ТЗ: фильтр подтверждения входа после wakeup-сигнала

## Цель

Уменьшить количество быстрых убыточных сделок `Bars Held <= 3`, которые возникают из-за раннего opposite SuperTrend flip после входа.

В режиме `long` проблема проявляется как:

```text
wakeup_flat_on_disallowed_st_flip
```

В режиме `revers` проблема проявляется как:

```text
wakeup_reverse_on_st_flip
```

Суть одна: вход происходит слишком рано, SuperTrend быстро переворачивается, сделка закрывается или разворачивается через `1-3` бара.

## Задача

Добавить в `wakeup_regime.entry` опциональный фильтр подтверждения SuperTrend перед фактическим входом.

После появления разрешенного wakeup entry signal стратегия не открывает позицию сразу, а переводит сигнал в состояние ожидания подтверждения.

## Конфиг

```yaml
trade_filter:
  wakeup_regime:
    entry:
      st_confirmation:
        enabled: true
        confirm_bars: 1
```

`confirm_bars: 0` должен полностью сохранять текущую логику.

## Логика для `trade_mode = long`

```text
1. Получен разрешенный LONG wakeup entry signal.
2. Ждем confirm_bars баров.
3. Если все confirm_bars SuperTrend остается GREEN, открываем LONG.
4. Если до подтверждения SuperTrend flipped RED, сигнал отменяется.
```

## Логика для `trade_mode = short`

```text
1. Получен разрешенный SHORT wakeup entry signal.
2. Ждем confirm_bars баров.
3. Если все confirm_bars SuperTrend остается RED, открываем SHORT.
4. Если до подтверждения SuperTrend flipped GREEN, сигнал отменяется.
```

## Логика для `trade_mode = revers`

Для `revers` фильтр должен работать симметрично по направлению сигнала:

```text
Если wakeup entry signal = LONG:
  ждем confirm_bars баров
  если SuperTrend остается GREEN -> открываем LONG
  если flipped RED -> отменяем LONG-сигнал

Если wakeup entry signal = SHORT:
  ждем confirm_bars баров
  если SuperTrend остается RED -> открываем SHORT
  если flipped GREEN -> отменяем SHORT-сигнал
```

Важно: в `revers` фильтр не должен автоматически открывать позицию в противоположную сторону при отмене сигнала. Он только подтверждает или отменяет исходный wakeup entry.

## Ожидаемое поведение

```text
confirm_bars = 0:
  текущая логика без изменений

confirm_bars > 0:
  вход откладывается
  направление входа фиксируется в pending state
  если ST сохраняет направление confirm_bars баров -> вход
  если ST переворачивается до подтверждения -> pending signal отменяется
```

## Pending state

Для ожидающего входа нужно хранить:

```text
pending_entry_active: bool
pending_entry_direction: LONG / SHORT
pending_entry_started_at_bar
pending_entry_age_bars
pending_entry_trigger_source
```

При отмене сигнала записывать диагностическую причину:

```text
st_confirmation_failed
```

При успешном подтверждении:

```text
st_confirmation_passed
```

## Сброс pending signal

Ожидающий сигнал должен сбрасываться при:

```text
opposite ST flip до подтверждения
daily_reset
time_filter_reset
FSM_OFF
wakeup cycle end
```

## Диагностика / экспорт

Добавить в `FilterDiagnostics_100` поля:

```text
Wakeup ST Confirmation Enabled
Wakeup ST Confirmation Bars
Wakeup ST Confirmation Pending
Wakeup ST Confirmation Direction
Wakeup ST Confirmation Age Bars
Wakeup ST Confirmation Passed
Wakeup ST Confirmation Failed
Wakeup ST Confirmation Cancel Reason
```

В `filters_summary` добавить счетчики:

```text
Wakeup ST Confirmation Started
Wakeup ST Confirmation Passed
Wakeup ST Confirmation Failed
Wakeup ST Confirmation Cancelled Reset
Wakeup ST Confirmation Cancelled Time Reset
```

## Что сравнить в отчетах

Для каждого режима `long`, `short`, `revers` прогнать:

```yaml
confirm_bars: 0
confirm_bars: 1
confirm_bars: 2
```

Сравнить:

```text
Sum PnL %
Profit Factor
Max Drawdown
Win Rate
Num Trades
Avg Trade
false starts count
Bars Held <= 3 count
Bars Held <= 3 Sum PnL %
Bars Held <= 3 Win Rate
```

Для `long` дополнительно:

```text
wakeup_flat_on_disallowed_st_flip count
wakeup_flat_on_disallowed_st_flip Sum PnL %
```

Для `revers` дополнительно:

```text
wakeup_reverse_on_st_flip count
wakeup_reverse_on_st_flip Sum PnL %
```

## Критерий успеха

Фильтр считается полезным, если:

```text
Bars Held <= 3 count снижается
false starts count снижается
Sum PnL по Bars Held <= 3 улучшается
Profit Factor не ухудшается
Max Drawdown не ухудшается
общий Sum PnL не падает критически
```

Главный целевой эффект: убрать ранние входы, после которых SuperTrend почти сразу делает opposite flip.
