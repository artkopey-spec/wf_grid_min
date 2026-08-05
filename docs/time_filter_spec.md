# Time trade filter — runtime specification v2

Документ фиксирует production-контракт `trade_filter.time_filter` после
перехода на несколько окон, calendar-cycle reset и Time-only режим.

## Активация и effective mode

Корневой `trade_filter.enabled: true` требует хотя бы один активный подфильтр.
Effective mode определяется только активными capabilities:

| ZigZag | Volume | Time | Effective mode |
|---|---|---|---|
| false | false | true | `time_only` |
| false | true | false | `volume_only` |
| false | true | true | `volume_and_time` |
| true | false | false | `zigzag_only` |
| true | false | true | `zigzag_and_time` |
| true | true | false | `zigzag_and_volume` |
| true | true | true | `zigzag_volume_and_time` |

При выключенном корне effective mode равен `disabled`. Inert ZigZag, Volume и
wakeup templates не активируют runtime и не влияют на effective fingerprint.

## Конфигурация

Новый формат:

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
  wakeup_regime:
    enabled: false
```

Legacy-форма одного окна остаётся допустимой:

```yaml
time_filter:
  enabled: true
  window: "09:00-12:00"
```

`window` и `windows` одновременно запрещены. Legacy `window` и одноэлементный
`windows` имеют одинаковый effective snapshot.

При `enabled: false` известные поля всё равно проходят schema/type validation,
но содержимое не активирует Time runtime.

## Валидация окон

Каждое окно имеет строгий формат `HH:MM-HH:MM` и трактуется как
полуинтервал `[start, end)`.

- часы: `00..23`;
- минуты: `00..59`;
- `start < end`;
- cross-midnight не поддерживается;
- список не может быть пустым;
- дубликаты, overlap и touching запрещены;
- после parsing окна сортируются по времени начала.

Touching, например `09:00-12:00` и `12:00-13:00`, запрещён намеренно: граница
между окнами является границей торговых циклов, а сливающиеся интервалы делали
бы reset-семантику неоднозначной.

## Timezone

Окна применяются в timezone индекса без неявной конвертации.

- tz-naive MOEX данные должны уже содержать локальное MSK-время;
- UTC-индекс требует окон в UTC;
- для tz-aware индекса timezone снимается только после сохранения отображаемого
  локального времени;
- DST-нормализация остаётся обязанностью data loader.

Time active требует монотонный `DatetimeIndex`. Time-only не требует `volume`,
ZigZag stats или VolumeRuntime.

## Membership и calendar-cycle reset

Бар находится внутри окна, если:

```text
start_minute <= minute_of_day(bar) < end_minute
```

Cycle identity:

```text
(локальная календарная дата, canonical window index)
```

`time_filter_reset_event[t] = true`, когда предыдущий существующий бар
принадлежал циклу, а текущий бар уже не принадлежит тому же циклу. Поэтому
reset возникает не только на первом outside-баре, но и при:

- переходе между разными окнами;
- смене календарной даты;
- gap, когда оба соседних существующих бара формально находятся внутри одного
  времени окна, но относятся к разным датам.

Oracle:

| Предыдущий бар | Текущий бар | Reset |
|---|---|---|
| outside | outside | false |
| outside | inside | false |
| inside, тот же date/window | inside | false |
| inside | outside | true |
| inside window A | inside window B | true |
| inside day D | inside day D+1 | true |

Первый бар полного runtime и первый бар любого slice всегда имеют
`reset_event=false`.

## Open-to-open исполнение и gap exposure

Reset — decision event на баре `t`. При существующей позиции flatten
исполняется на следующем доступном open:

```text
position[t] != 0
reset[t] = true
position[t+1] = 0
```

Reset-бар может ещё содержать старую позицию. Если reset находится на последнем
баре, исполненного выхода нет и `filter_time_reset` не назначается.

Синтетические/pre-open исполнения не создаются. Поэтому PnL ценового gap до
следующего существующего open сохраняется — Time filter не устраняет gap
exposure.

## Entry attempts

Time-only создаёт entry attempt при:

```text
ненулевом новом raw target
OR открытии разрешённого cycle
OR pending re-entry после reset
```

Пассивные outside-бары не создают повторяющихся block reasons. После возврата в
окно re-entry возможен без нового SuperTrend flip.

В owner modes Time gate применяется к реальным attempt events владельца:

- ZigZag — к ZigZag lifecycle entry attempts;
- Volume — к Volume cycle start attempts;
- Time-only — к формуле выше.

## Reset priority и exit attribution

Приоритет reset reasons:

1. `daily_reset`;
2. `time_filter_reset`;
3. owner-specific reason.

Приоритет trade exit attribution:

1. `filter_daily_reset`;
2. `filter_time_reset`;
3. owner-specific forced exit;
4. `pending_open_trade_at_end`;
5. baseline/FSM exit.

`filter_time_reset` означает только фактически исполненный переход
`nonzero → zero`.

## Precompute и slicing

Direct, WF и Tester строят `TimeFilterRuntime` на полном датасете один раз.
WF/tester periods получают slice готовых массивов. Slice:

- не парсит schedule повторно;
- не нормализует timestamps повторно;
- очищает первый reset;
- не наследует position/FSM state из предыдущего периода.

В multiprocessing runtime передаётся через worker initializer, а не в каждой
grid task.

## Diagnostics capabilities

Common при любом active filter:

```text
trade_filter_enabled
trade_filter_mode
filter_allowed_entry
filter_block_reason
daily_reset_event
```

Time capability:

```text
time_filter_enabled
time_filter_in_window
time_filter_reset_event
```

ZigZag, Volume и wakeup поля присутствуют только при соответствующей active
capability. Time-only не создаёт фиктивные `trade_filter_state`,
`trade_filter_trigger_source`, median/ZZ stop или Volume arrays.

Summary Time-поля:

```text
time_filter_reset_count
time_filter_bars_in_window
time_filter_bars_out_window
time_filter_blocked_entry_count
time_filter_forced_exit_count
```

Cycle export для Time-only имеет metadata status `not_applicable`.

## Snapshots и fingerprint

`trade_filter_public_snapshot(tf)` содержит только пользовательские поля и
сохраняет исходный порядок `windows`. Ключи, начинающиеся с `_`, запрещены.

`trade_filter_effective_snapshot(tf)` содержит:

```text
effective_mode
zigzag_active
volume_active
time_filter_active
canonical_windows_minutes
time_reset_semantics = calendar_cycle_close_reset
```

Effective snapshot используется в WF fingerprint и Tester_Config. Порядок
окон, legacy/modern запись одного окна и inactive templates не меняют effective
identity.

## Tester batch limitation

Низкоуровневый `equal_blocks` с active trade filter остаётся unsupported.
Canonical batch не считает это общим failure: он выводит warning, записывает

```text
status = skipped_unsupported
reason = trade_filter_equal_blocks_not_supported
```

и продолжает legacy-period run.

## Migration

Active Time теперь создаёт reset при переходе между календарными датами или
разными окнами, даже когда два соседних существующих бара оба входят в
разрешённое время. Это принятая breaking migration для Time-on результатов:
overnight positions, returns, trades и exit reasons могут измениться.

Time-off режимы сохраняют прежнее поведение. Подробный checklist и rollback:
[time_filter_multi_window_migration.md](time_filter_multi_window_migration.md).
