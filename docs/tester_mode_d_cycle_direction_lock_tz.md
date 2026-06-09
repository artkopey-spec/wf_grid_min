**ТЗ: Tester-Only Mode D Cycle Direction Lock**

**Цель**

Добавить в `tester` opt-in режим для `zigzag.mode: D`: активный `wakeup_regime` cycle должен сохранять направление, зафиксированное на баре фактического старта цикла, и не разворачиваться внутренними SuperTrend flip-ами.

`wf_grid` runtime не менять. Обычные режимы `A / B / C / A+B / C+B` не менять.

Допускается аккуратное изменение shared `trade_filter` config schema/dataclasses, потому что они общие для `tester` и `wf_grid`, но политика `wf_grid` должна сохраниться: `mode D`, `exit C`, `wakeup_regime` остаются недоступными в `wf_grid`.

**Конфиг**

Добавить поле:

```yaml
trade_filter:
  wakeup_regime:
    lock_cycle_direction: true
```

Default:

```yaml
lock_cycle_direction: false
```

Отсутствие поля эквивалентно `false`.

Поле должно быть строго boolean. Значения строкой (`"true"`), числом (`1`) или `null` должны отвергаться validator-ом.

Полный пример валидного tester-конфига:

```yaml
trade_mode: revers

trade_filter:
  enabled: true
  type: zigzag_st_mode

  zigzag:
    enabled: true
    mode: D
    reversal_threshold: 0.01
    candidate_trigger_threshold: 0.05
    global_stats_source: full_dataset
    leg_height_mode: pct
    global_median: auto
    local_window: 5

  lifecycle:
    exit_off_mode: exit C

  wakeup_regime:
    enabled: true
    lock_cycle_direction: true

    entry:
      candidate_height:
        enabled: true
        quantile: 0.65
      candidate_age:
        enabled: true
        max_bars: 20
      atr_expansion:
        enabled: false
        short_window: 5
        long_window: 20
        min_ratio: 1.1
      volume_expansion:
        enabled: false
        short_window: 5
        baseline_window: 20
        min_ratio: 1.1

    exit:
      ttl:
        enabled: true
        bars: 100
      no_fresh_candidate:
        enabled: false
        quantile: 0.60
        max_age_bars: 20
        timeout_bars: 30
      action:
        mode: block_new_entries
```

`trade_mode: both` и `trade_mode: revers` семантически разрешают оба направления и реверс в старом поведении. Acceptance для runtime может использовать `both`; для CLI-сценариев использовать `revers`, так как CLI `--mode` может не принимать `both`.

**Область Действия**

Флаг применяется только когда одновременно:

```text
caller_pipeline = tester
trade_filter.enabled = true
trade_filter.zigzag.mode = D
trade_filter.wakeup_regime.enabled = true
trade_filter.wakeup_regime.lock_cycle_direction = true
```

При `lock_cycle_direction: false` поведение должно остаться полностью старым.

Для `wf_grid`:

```text
zigzag.mode: D        -> reject
lifecycle.exit C      -> reject
wakeup_regime present -> reject
```

**Источник Направления Цикла**

Единственный источник направления:

```text
per_bar.candidate_leg_direction[t_start]
```

Где `t_start` - бар фактического старта wakeup-cycle.

Значения:

```text
+1 = растущая ZZ candidate-нога
-1 = падающая ZZ candidate-нога
 0 = направление неизвестно
```

Если:

```text
candidate_leg_direction[t_start] == 0
```

wakeup-entry запрещён.

Запрещено использовать для направления цикла:

```text
st_flip_dir
trend
held_pos
volume_initial_direction
last confirmed ZZ leg direction
candidate_leg_direction на барах после старта
```

**Старт Цикла**

Wаkeup-cycle стартует только если одновременно:

```text
state_at_bar_start == OFF
not reset bar
time_filter permits entry
candidate_leg_direction[t] in {-1, +1}
trade_mode allows candidate_leg_direction[t]
all enabled wakeup entry components passed
```

Компоненты entry:

```text
candidate_height
candidate_age
atr_expansion
volume_expansion
```

Учитываются только компоненты с `enabled: true`.

На баре фактического старта:

```text
t_start = t
cycle_direction = candidate_leg_direction[t_start]
wakeup_active_direction = cycle_direction
held_pos = cycle_direction
trade_filter_trigger_source = wakeup_regime
state = ST_ACTIVE_FREEZE
```

Позиция применяется по open-to-open контракту:

```text
positions[t_start + 1] = cycle_direction
```

Если на `t_start` есть same-bar ST flip в противоположную сторону, он не имеет права перезаписать направление старта.

Пример:

```text
candidate_leg_direction[t_start] = +1
st_flip_dir[t_start] = -1

result:
cycle_direction = +1
held_pos = +1
positions[t_start + 1] = +1
wakeup_active_direction = +1
```

**Поведение При lock_cycle_direction: false**

Старое поведение сохраняется:

```text
trade_mode: both/revers
  internal ST flip может развернуть позицию
  wakeup_active_direction может измениться на flip_dir
  wakeup_position_action = reverse_on_st_flip

trade_mode: long
  short ST flip флетит позицию
  long ST flip восстанавливает long

trade_mode: short
  long ST flip флетит позицию
  short ST flip восстанавливает short
```

**Поведение При lock_cycle_direction: true**

После старта:

```text
cycle_direction
```

неизменен до завершения lifecycle.

`wakeup_active_direction` в этом режиме должен отражать зафиксированное направление цикла и не изменяться от внутренних ST flip-ов.

Новые значения:

```text
candidate_leg_direction[t > t_start]
```

не могут изменить направление текущего цикла.

Internal ST flip в сторону `cycle_direction`:

```text
held_pos = cycle_direction
wakeup_active_direction = cycle_direction
wakeup_position_action = restore_allowed_position_on_st_flip
```

Если позиция была flat, она восстанавливается в сторону цикла.

Internal ST flip против `cycle_direction`:

```text
held_pos = 0
wakeup_active_direction = cycle_direction
wakeup_position_action = flat_on_disallowed_st_flip
```

Реверса нет. Цикл остаётся активным.

Для первого эксперимента новые action values не вводить. Использовать существующие:

```text
flat_on_disallowed_st_flip
restore_allowed_position_on_st_flip
```

Пример long-cycle:

```text
cycle_direction = +1

ST flip +1 -> held_pos = +1
ST flip -1 -> held_pos = 0
ST flip +1 -> held_pos = +1

short position внутри цикла запрещена
wakeup_active_direction всегда +1
```

Пример short-cycle:

```text
cycle_direction = -1

ST flip -1 -> held_pos = -1
ST flip +1 -> held_pos = 0
ST flip -1 -> held_pos = -1

long position внутри цикла запрещена
wakeup_active_direction всегда -1
```

Даже при:

```text
trade_mode: both
trade_mode: revers
```

`lock_cycle_direction: true` запрещает реверс внутри уже активного wakeup-cycle.

**Взаимодействие С trade_mode**

`trade_mode` остаётся верхним ограничителем только для старта цикла:

```text
trade_mode: long   разрешает только cycle_direction = +1
trade_mode: short  разрешает только cycle_direction = -1
trade_mode: both   разрешает +1 и -1
trade_mode: revers разрешает +1 и -1
```

После старта при `lock_cycle_direction: true` `both/revers` не разрешают реверс внутри текущего цикла.

**Exit C**

Exit-логика не меняется:

```text
ttl
no_fresh_candidate
action.mode: block_new_entries
action.mode: close_position
daily_reset
time_filter_reset
```

Lock direction влияет только на internal ST flip handling во время активного wakeup-cycle.

Если на одном баре одновременно срабатывают Exit C и ST flip, приоритет остаётся за Exit C. Lock-логика ST flip на таком баре не должна восстанавливать, флетить или разворачивать позицию.

**Диагностика**

Существующие поля сохранить:

```text
wakeup_active_direction
wakeup_position_action
wakeup_regime_active
```

Добавить per-bar diagnostic field:

```text
wakeup_lock_cycle_direction_config
```

Контракт:

```text
dtype: int8
values:
  1 = lock_cycle_direction enabled for this Mode D run
  0 = disabled
```

Поле экспортируется только для Mode D wakeup diagnostics вместе с остальными `wakeup_*` полями.

Если включён retained per-bar diagnostics export, поле должно проходить в export. Если есть централизованный список display names, добавить человекочитаемое имя:

```text
Wakeup Lock Cycle Direction Config
```

**Acceptance Tests**

1. Старое поведение сохраняется

```text
Mode D
trade_mode: both или revers
lock_cycle_direction: false

long-cycle стартует
opposite ST flip разворачивает позицию в short
wakeup_position_action = reverse_on_st_flip
wakeup_active_direction меняется на -1
```

2. Long lock

```text
Mode D
trade_mode: both или revers
lock_cycle_direction: true
candidate_leg_direction[t_start] = +1

cycle starts long
opposite ST flip -1 закрывает во flat
wakeup_active_direction остаётся +1
следующий ST flip +1 восстанавливает long
short position внутри цикла не появляется
```

3. Short lock

```text
Mode D
trade_mode: both или revers
lock_cycle_direction: true
candidate_leg_direction[t_start] = -1

cycle starts short
opposite ST flip +1 закрывает во flat
wakeup_active_direction остаётся -1
следующий ST flip -1 восстанавливает short
long position внутри цикла не появляется
```

4. `trade_mode: long`, short candidate

```text
candidate_leg_direction[t_start] = -1
cycle не стартует
positions[t_start + 1] = 0
```

5. `trade_mode: short`, long candidate

```text
candidate_leg_direction[t_start] = +1
cycle не стартует
positions[t_start + 1] = 0
```

6. Same-bar opposite ST flip

```text
candidate_leg_direction[t_start] = +1
st_flip_dir[t_start] = -1
lock_cycle_direction: true

cycle всё равно long
held_pos = +1
wakeup_active_direction = +1
positions[t_start + 1] = +1
```

7. Exit C priority

```text
active wakeup-cycle
lock_cycle_direction: true
Exit C fires on same bar as ST flip

Exit C action wins
ST flip lock action не применяется
wakeup_position_action reflects Exit C action
```

8. Config loader

```text
lock_cycle_direction absent -> parsed/default false
lock_cycle_direction true -> accepted in tester
lock_cycle_direction false -> accepted in tester
lock_cycle_direction non-bool -> rejected
unknown sibling keys still rejected
```

9. Diagnostics

```text
Mode D diagnostics include wakeup_lock_cycle_direction_config
lock false -> all values 0
lock true -> all values 1
non-Mode-D diagnostics do not gain wakeup diagnostics
```

10. `wf_grid` rejection

```text
wf_grid rejects mode D
wf_grid rejects exit C
wf_grid rejects wakeup_regime, including lock_cycle_direction
```

**Implementation Boundaries**

Разрешено менять:

```text
donor/supertrend_optimizer/core/trade_filter_config.py
donor/supertrend_optimizer/core/zigzag_st_filter.py
tester-related diagnostics/export mappings
tests
```

Не менять поведение:

```text
Mode A/B/C/A+B/C+B
general ZigZag candidate_leg_direction calculation
WF runtime support for Mode D
WF pipeline
Exit C semantics
open-to-open position contract
```

**Нецели**

Не превращать lock в глобальный `trade_mode`.

Не запрещать future cycles противоположного направления: ограничение действует только внутри текущего активного wakeup-cycle.

Не менять расчёт `candidate_leg_direction`.

Не вводить новые action values в первой реализации.
