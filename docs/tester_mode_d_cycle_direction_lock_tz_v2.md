**ТЗ v2: Tester-Only Mode D Cycle Direction Lock**

**Цель**

Добавить в `tester` opt-in режим для `zigzag.mode: D`: активный `wakeup_regime` cycle должен сохранять направление, зафиксированное на баре фактического старта цикла, и не разворачиваться внутренними SuperTrend flip-ами.

Изменение предназначено для проверки гипотезы в tester.

`wf_grid` runtime не менять. Обычные режимы `A / B / C / A+B / C+B` не менять.

Допускается аккуратное изменение shared `trade_filter` config schema/dataclasses, потому что они общие для `tester` и `wf_grid`, но политика `wf_grid` должна сохраниться:

```text
zigzag.mode: D        -> reject
lifecycle.exit C      -> reject
wakeup_regime present -> reject
```

Tester-only обеспечивается validation layer. Не протаскивать `caller_pipeline` в runtime и не добавлять runtime-ветвление по `caller_pipeline` в `apply()`.

**Конфиг**

Добавить поле:

```yaml
trade_filter:
  wakeup_regime:
    lock_cycle_direction: true
```

Точный путь поля:

```text
trade_filter.wakeup_regime.lock_cycle_direction
```

Default:

```yaml
lock_cycle_direction: false
```

Отсутствие поля эквивалентно `false`.

Поле должно быть строго boolean. Значения строкой (`"true"`), числом (`1`) или `null` должны отвергаться validator-ом.

Если `wakeup_regime.enabled: false`, поле `lock_cycle_direction` всё равно проходит strict bool validation, но runtime effect отсутствует. Отдельную ошибку вида `lock requires wakeup_regime.enabled=true` не вводить.

Для реализации config path обновить:

```text
TradeFilterWakeupRegimeConfig
build_trade_filter_config_from_raw
TRADE_FILTER_ALLOWED_KEYS["trade_filter.wakeup_regime"]
_validate_wakeup_regime_block strict bool validation
```

Unknown sibling keys внутри `trade_filter.wakeup_regime` по-прежнему должны отвергаться.

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

Флаг влияет на runtime только когда одновременно:

```text
trade_filter.enabled = true
trade_filter.zigzag.mode = D
trade_filter.wakeup_regime.enabled = true
trade_filter.wakeup_regime.lock_cycle_direction = true
```

При `lock_cycle_direction: false` поведение должно остаться полностью старым.

Для `wf_grid`:

```text
mode D остаётся недоступным
exit C остаётся недоступным
wakeup_regime остаётся недоступным
lock_cycle_direction не делает wakeup_regime допустимым в wf_grid
```

**Источник Направления Цикла**

Единственный источник направления цикла:

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

Это уже должно быть обеспечено entry evaluation через `direction_ok`. Новую параллельную runtime-защиту не добавлять; acceptance test должен фиксировать существующий инвариант.

Запрещено использовать для направления цикла:

```text
st_flip_dir
trend
held_pos
volume_initial_direction
last confirmed ZZ leg direction
candidate_leg_direction на барах после старта
```

**Cycle Direction State**

В runtime для Mode D lock требуется хранить loop-local состояние:

```text
cycle_direction
```

Смысл:

```text
0  = нет активного locked wakeup-cycle
+1 = активный locked long-cycle
-1 = активный locked short-cycle
```

`cycle_direction` захватывается только в момент фактического старта wakeup-cycle:

```text
state_at_bar_start == OFF
state -> ST_ACTIVE_FREEZE
trade_filter_trigger_source = wakeup_regime
```

На баре `t_start`:

```text
cycle_direction = candidate_leg_direction[t_start]
wakeup_active_direction = cycle_direction
held_pos = cycle_direction
```

`cycle_direction` не изменяется внутренними ST flip-ами и новыми значениями `candidate_leg_direction` после старта.

Под `lock_cycle_direction: true` runtime invariant:

```text
wakeup_active_direction == cycle_direction
```

Инвариант действует во время активного locked wakeup-cycle и в `ST_STOPPING`, пока cycle direction сохраняется как runtime/diagnostic context. В `OFF` оба значения должны быть сброшены или находиться в существующем OFF-default состоянии.

Единственный источник истины для effective direction в lock-mode:

```text
cycle_direction
```

`wakeup_active_direction` под lock является diagnostic/runtime отражением `cycle_direction`, а не отдельным источником направления.

`cycle_direction` сбрасывается в `0` во всех точках фактического завершения wakeup lifecycle:

```text
daily_reset / combined reset
time_filter_reset / combined reset
Exit C action.mode: close_position, когда state становится OFF
любой переход, после которого Mode D wakeup-cycle фактически больше не активен
```

В текущем runtime обязательно покрыть все OFF-sites Mode D:

```text
combined-reset wipe -> state = OFF
Exit C close_position -> state = OFF
ST_STOPPING normalization when cur_pos == 0 -> state = OFF
ST_STOPPING close on opposite flip -> state = OFF
```

По текущему расположению кода два последних OFF-sites находятся после основного wakeup diagnostics / flip-handling блока. Их нельзя пропустить: при `Exit C action.mode: block_new_entries` `cycle_direction` должен сохраняться в `ST_STOPPING`, но обязан сброситься при фактическом `OFF`.

При `Exit C action.mode: block_new_entries` и переходе в `ST_STOPPING` `cycle_direction` сохраняется до фактического перехода в `OFF`, но lock-обработка ST flip-ов в `ST_STOPPING` не применяется.

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

Это уже должно быть структурно гарантировано тем, что internal ST flip handling выполняется только если на старте бара lifecycle уже был активен:

```text
state_at_bar_start == ST_ACTIVE_FREEZE
```

На баре старта:

```text
state_at_bar_start == OFF
```

Поэтому отдельную новую same-bar защиту не добавлять; acceptance test должен фиксировать существующий инвариант.

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

При `lock_cycle_direction: false` не менять семантику `wakeup_active_direction`, `wakeup_position_action`, `positions`, `no_fresh_candidate` и существующих diagnostics.

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

Internal ST flip handling при активном lock должен использовать `cycle_direction`, а не исходный `trade_mode`, как эффективное ограничение направления:

```text
cycle_direction = +1 -> effective direction mode = long
cycle_direction = -1 -> effective direction mode = short
```

Реализовать без новых action values.

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

Под `trade_mode: both/revers` при `lock_cycle_direction: true` эти action values начнут появляться в ситуациях, где раньше появлялся `reverse_on_st_flip`. Это ожидаемое поведение lock-mode. Потребители `wakeup_position_action` должны быть проверены на отсутствие неявного предположения, что `both/revers` всегда означает только `reverse_on_st_flip`.

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

`trade_mode` остаётся верхним ограничителем старта цикла:

```text
trade_mode: long   разрешает только cycle_direction = +1
trade_mode: short  разрешает только cycle_direction = -1
trade_mode: both   разрешает +1 и -1
trade_mode: revers разрешает +1 и -1
```

После старта при `lock_cycle_direction: true` `both/revers` не разрешают реверс внутри текущего цикла.

При `trade_mode: long/short` `lock_cycle_direction` семантически нейтрален для internal ST flip handling, потому что текущее поведение уже совпадает с locked-направлением:

```text
trade_mode: long
  short flip -> flat
  long flip -> long

trade_mode: short
  long flip -> flat
  short flip -> short
```

Отдельную новую ветку для `long/short` не создавать, если существующий helper может переиспользоваться без изменения поведения.

**Exit C И Freshness**

Exit-механики не меняются:

```text
ttl
no_fresh_candidate
action.mode: block_new_entries
action.mode: close_position
daily_reset
time_filter_reset
```

Lock direction влияет только на internal ST flip handling во время активного wakeup-cycle и на reference direction, относительно которого считается fresh candidate.

При `lock_cycle_direction: false` `no_fresh_candidate` должен сохранять старую семантику:

```text
freshness reference = текущий wakeup_active_direction
```

При `lock_cycle_direction: true` `no_fresh_candidate` должен считаться относительно зафиксированного направления цикла:

```text
freshness reference = cycle_direction
```

Это ожидаемое следствие lock-а. Формула freshness не меняется, но `active_direction` reference под lock заморожен.

Freshness under lock должен возникать как следствие заморозки:

```text
wakeup_active_direction = cycle_direction
```

Не вводить отдельную ветку freshness и не протаскивать `cycle_direction` отдельным аргументом в freshness helper. Это предотвратит появление второго источника истины. Acceptance tests должны фиксировать эмерджентное поведение через `wakeup_active_direction`.

Если на одном баре одновременно срабатывают Exit C и ST flip, приоритет остаётся за Exit C. Lock-логика ST flip на таком баре не должна восстанавливать, флетить или разворачивать позицию.

Это уже должно быть обеспечено тем, что internal ST flip handling подавляется, если Exit C сработал на этом баре. Новую параллельную защиту не добавлять; acceptance test должен фиксировать существующий инвариант.

**ST_STOPPING**

При `Exit C action.mode: block_new_entries` wakeup-cycle может перейти в `ST_STOPPING`.

В `ST_STOPPING`:

```text
lock_cycle_direction не применяет internal ST flip handling
новые ST flip-ы не восстанавливают позицию по cycle_direction
cycle_direction сохраняется как diagnostic/runtime context до фактического OFF
позиция доживает по существующей ST_STOPPING логике
```

При фактическом переходе в `OFF`:

```text
cycle_direction = 0
wakeup_active_direction = 0 или существующее OFF-default значение
```

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

Жёсткое runtime/export требование:

```text
wakeup_lock_cycle_direction_config
```

должно добавляться только внутри Mode-D-gated diagnostics блока вместе с остальными wakeup diagnostics. Для non-Mode-D diagnostics новое поле появляться не должно.

Если включён retained per-bar diagnostics export, поле должно проходить в export.

Если есть централизованный список display names, добавить человекочитаемое имя:

```text
Wakeup Lock Cycle Direction Config
```

Обновить diagnostics/export contracts и связанные tests, если они фиксируют полный набор колонок, display names или порядок Mode D wakeup diagnostics.

**Downstream Consumers**

Подтверждённый contract blast radius:

```text
donor/supertrend_optimizer/io/excel_tester.py
  add display-name pair:
    wakeup_lock_cycle_direction_config -> Wakeup Lock Cycle Direction Config

wf_grid/tests/test_pr5_schema_contract.py
  update _EXCEL_PER_BAR_HEADERS_SNAPSHOT with the same display-name pair
```

Это единственная обязательная синхронная правка frozen contract snapshot.

Не требуется править:

```text
wf_grid/tests/test_pr6_excel_contract.py
wf_grid/tests/test_wp9_diagnostics_export.py
wf_grid/ranking/scoring.py
donor/supertrend_optimizer/core/filter_trade_diagnostics.py
wf_grid/export/xlsx_writer.py
```

Пояснения:

```text
retained per-bar export работает passthrough по diag.items()
display-name lookup fallback-safe, но exact snapshot требует обновления имени
scoring.py wakeup_position_action не читает
filter_trade_diagnostics.py обрабатывает wakeup_position_action как строковую метку без ветвления по trade_mode
```

`wf_grid/tests/test_pr5_schema_contract.py::test_per_bar_keyset_exact_equality` не должен сломаться, потому что он использует non-Mode-D fixture. Это верно только если новое поле добавлено строго в Mode-D-gated diagnostics block.

Особенно проверить, что downstream не предполагает:

```text
trade_mode both/revers -> wakeup_position_action только reverse_on_st_flip
```

Под lock-mode для `both/revers` допустимы:

```text
flat_on_disallowed_st_flip
restore_allowed_position_on_st_flip
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
wakeup_position_action использует flat_on_disallowed_st_flip / restore_allowed_position_on_st_flip
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
wakeup_position_action использует flat_on_disallowed_st_flip / restore_allowed_position_on_st_flip
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

6. Unknown candidate direction

```text
candidate_leg_direction[t_start] = 0
cycle не стартует
positions[t_start + 1] = 0
```

Этот тест фиксирует существующий entry invariant. Новую отдельную runtime-защиту не добавлять, если текущая entry evaluation уже запрещает direction 0.

7. Same-bar opposite ST flip

```text
candidate_leg_direction[t_start] = +1
st_flip_dir[t_start] = -1
lock_cycle_direction: true

cycle всё равно long
held_pos = +1
wakeup_active_direction = +1
positions[t_start + 1] = +1
```

Этот тест фиксирует существующий same-bar invariant через `state_at_bar_start`. Новую отдельную same-bar защиту не добавлять.

8. Exit C priority

```text
active wakeup-cycle
lock_cycle_direction: true
Exit C fires on same bar as ST flip

Exit C action wins
ST flip lock action не применяется
wakeup_position_action reflects Exit C action
```

Этот тест фиксирует существующий Exit C priority invariant. Новую отдельную защиту не добавлять, если текущий Exit C gate уже подавляет internal ST flip.

9. `no_fresh_candidate` reference under lock

```text
Mode D
trade_mode: both или revers
lock_cycle_direction: true
no_fresh_candidate.enabled: true
cycle_direction = +1
opposite ST flip -1 произошёл внутри цикла

freshness продолжает считаться относительно +1
wakeup_active_direction остаётся +1
no_fresh_candidate timeout behavior соответствует locked reference
```

10. `no_fresh_candidate` old behavior without lock

```text
Mode D
trade_mode: both или revers
lock_cycle_direction: false
no_fresh_candidate.enabled: true
opposite ST flip -1 произошёл внутри цикла

wakeup_active_direction меняется на -1
freshness считается относительно -1
старое no_fresh_candidate поведение сохраняется
```

11. ST_STOPPING behavior

```text
active wakeup-cycle
lock_cycle_direction: true
Exit C action.mode: block_new_entries
state -> ST_STOPPING
ST flip occurs while in ST_STOPPING

lock ST flip handling не применяется
позиция доживает по существующей ST_STOPPING логике
cycle_direction сбрасывается только при фактическом OFF
```

12. Config loader

```text
lock_cycle_direction absent -> parsed/default false
lock_cycle_direction true -> accepted in tester
lock_cycle_direction false -> accepted in tester
lock_cycle_direction non-bool -> rejected
lock_cycle_direction true with wakeup_regime.enabled false -> accepted as no-op after strict bool validation
unknown sibling keys still rejected
```

13. Diagnostics

```text
Mode D diagnostics include wakeup_lock_cycle_direction_config
lock false -> all values 0
lock true -> all values 1
non-Mode-D diagnostics do not gain wakeup diagnostics
retained per-bar diagnostics export includes wakeup_lock_cycle_direction_config
display name is present if display-name registry exists
```

Retained per-bar diagnostics export должен проходить без правки writer-а, через существующий passthrough. Допустимо расширить существующий passthrough test новой колонкой.

14. Downstream action values

```text
Mode D
trade_mode: both или revers
lock_cycle_direction: true
internal opposite / restore ST flips occur

wakeup_position_action may contain:
  flat_on_disallowed_st_flip
  restore_allowed_position_on_st_flip

exports / trade diagnostics / summaries do not assume reverse_on_st_flip only
```

15. `wf_grid` rejection

```text
wf_grid rejects mode D
wf_grid rejects exit C
wf_grid rejects wakeup_regime
wf_grid rejects wakeup_regime with lock_cycle_direction
```

**Implementation Boundaries**

Разрешено менять:

```text
donor/supertrend_optimizer/core/trade_filter_config.py
donor/supertrend_optimizer/core/zigzag_st_filter.py
donor/supertrend_optimizer/io/excel_tester.py
tester-related diagnostics/export mappings
tests
```

Не менять поведение:

```text
Mode A/B/C/A+B/C+B
general ZigZag candidate_leg_direction calculation
WF runtime support for Mode D
WF pipeline
open-to-open position contract
lock=false Mode D behavior
```

Exit C mechanics не менять, но под `lock_cycle_direction: true` явно зафиксировать, что `no_fresh_candidate` использует locked `cycle_direction` как reference direction.

**Implementation Notes**

Минимальная runtime-правка должна быть сосредоточена вокруг Mode D internal ST flip handling.

Рекомендуемый подход:

```text
1. Добавить config flag и diagnostics flag.
2. Ввести loop-local cycle_direction.
3. На wakeup start захватить cycle_direction = cand_dir_t.
4. При lock=false оставить старый вызов _apply_mode_d_internal_st_flip без изменения поведения.
5. При lock=true применять effective direction derived from cycle_direction:
     +1 -> long-like behavior
     -1 -> short-like behavior
6. Не добавлять caller_pipeline в apply().
7. Не добавлять новые action values.
8. Сбросить cycle_direction во всех фактических OFF/reset/close paths.
9. Не добавлять отдельные guards для already-existing invariants:
     candidate direction 0 entry block
     same-bar opposite ST flip on start bar
     Exit C priority over ST flip
10. Добавить wakeup_lock_cycle_direction_config строго в Mode-D-gated diagnostics block.
11. Обновить FILTER_DIAGNOSTICS_100_DISPLAY_NAMES и exact snapshot в test_pr5_schema_contract.py.
12. Не править xlsx_writer.py, scoring.py, filter_trade_diagnostics.py, test_pr6_excel_contract.py и test_wp9_diagnostics_export.py без отдельной фактической причины.
```

**Нецели**

Не превращать lock в глобальный `trade_mode`.

Не запрещать future cycles противоположного направления: ограничение действует только внутри текущего активного wakeup-cycle.

Не менять расчёт `candidate_leg_direction`.

Не вводить новые action values в первой реализации.

Не протаскивать `caller_pipeline` в runtime.

Не добавлять WF runtime support для Mode D.
