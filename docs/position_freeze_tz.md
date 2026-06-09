# ТЗ: Position Freeze для wakeup_regime

## Цель

Добавить режим заморозки позиции после входа или разворота внутри `wakeup_regime`.

Проблема проявляется по-разному в разных `trade_mode`:

- `long`: быстрый opposite ST flip закрывает long в flat через `flat_on_disallowed_st_flip`.
- `short`: симметрично, быстрый opposite ST flip закрывает short в flat.
- `revers`: быстрый opposite ST flip сразу разворачивает позицию через `reverse_on_st_flip`.

По отчету `test_result_single_20260609_144020.xlsx` для режима `revers` проблема актуальна:

- `wakeup_reverse_on_st_flip`: 1958 сделок, суммарно `-92.97%`, PF `0.26`;
- быстрые reverse-сделки `Bars Held <= 3`: 982 сделки, суммарно `-75.22%`, win rate `5.30%`;
- `wakeup_exit_ttl`, наоборот, прибыльный: `+94.34%`, PF `4.54`.

## Конфиг

```yaml
trade_filter:
  wakeup_regime:
    position_freeze:
      enabled: true
      min_hold_bars: 3
      apply_to: internal_opposite_st_flip
      release_action: apply_if_still_opposite
```

## Общая логика

После каждого открытия позиции или разворота сохранять:

```text
frozen_position_direction = текущая позиция
position_entry_bar_index = текущий бар
freeze_until = entry_bar_index + min_hold_bars
```

Пока freeze активен:

- если приходит внутренний opposite ST flip, не менять позицию;
- оставить текущую позицию открытой;
- записать диагностическое событие `wakeup_position_freeze_ignored_opposite_st_flip`.

После окончания freeze:

- если ST снова совпадает с текущей позицией, ничего не делать;
- если ST все еще opposite, применить действие согласно `trade_mode`.

## Поведение по trade_mode

### long

```text
LONG + ST flip SHORT во время freeze -> держим LONG
после freeze, если ST все еще SHORT -> закрыть в flat
```

Итоговое действие после release:

```text
flat_on_disallowed_st_flip
```

### short

```text
SHORT + ST flip LONG во время freeze -> держим SHORT
после freeze, если ST все еще LONG -> закрыть в flat
```

Итоговое действие после release:

```text
flat_on_disallowed_st_flip
```

### revers

```text
LONG + ST flip SHORT во время freeze -> держим LONG
после freeze, если ST все еще SHORT -> reverse LONG -> SHORT

SHORT + ST flip LONG во время freeze -> держим SHORT
после freeze, если ST все еще LONG -> reverse SHORT -> LONG
```

Итоговое действие после release:

```text
reverse_on_st_flip
```

## Исключения

Freeze не должен блокировать:

- `daily_reset`;
- `time_filter_reset`;
- конец данных;
- аварийный stop/max loss, если он есть.

`wakeup_exit_ttl` и `wakeup_exit_no_fresh_candidate` рекомендуется оставить вне freeze на первом этапе, чтобы не ломать уже прибыльную TTL-логику в `revers`.

## Диагностика

Добавить в `FilterDiagnostics_100`:

```text
Wakeup Position Freeze Active
Wakeup Position Freeze Bars Left
Wakeup Position Freeze Ignored Opposite ST Flip
Wakeup Position Freeze Release Action
```

Добавить счетчики в summary/filters_summary:

```text
wakeup_position_freeze_ignored_opposite_st_flip_count
wakeup_position_freeze_release_flat_count
wakeup_position_freeze_release_reverse_count
```

## Тестовые прогоны

Проверить:

```text
baseline
min_hold_bars = 2
min_hold_bars = 3
min_hold_bars = 4
```

Отдельно сравнить режимы:

```text
long
short
revers
```

## Метрики сравнения

Сравнивать:

- `Sum PnL %`;
- `Profit Factor`;
- `Max Drawdown`;
- `Avg Trade`;
- количество сделок `Bars Held <= 3`;
- `false starts count`;
- `wakeup_flat_on_disallowed_st_flip count`;
- `wakeup_reverse_on_st_flip count`;
- `wakeup_exit_ttl count`;
- PnL по `wakeup_flat_on_disallowed_st_flip`;
- PnL по `wakeup_reverse_on_st_flip`.

Главная метрика для `long`/`short`: уменьшился ли убыток и количество быстрых `flat_on_disallowed_st_flip`.

Главная метрика для `revers`: уменьшился ли убыток и количество быстрых `reverse_on_st_flip` с `Bars Held <= 3`.
