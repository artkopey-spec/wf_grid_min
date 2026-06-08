**ТЗ v6: Mode D / wakeup_regime при ST flip**

**Цель**

В Mode D `wakeup_regime` является активным режимом, а не одноразовым входом.

```text
wakeup entry -> open position
ST flip inside active wakeup -> position action
wakeup-cycle remains active
ttl / no_fresh_candidate / reset -> end wakeup-cycle
```

`opposite_st_flip` внутри active wakeup-cycle не является wakeup exit.

**Scope**

Реализовать единый контракт для `trade_mode`:

```text
revers
both
long
short
```

Обязательный scope:

```text
tester diagnostics
tester summary
tester Excel
trade-level diagnostics
Mode D unit/regression tests
```

`wf_grid` pipeline этим ТЗ не должен получать новую поддержку Mode D, если Mode D сейчас запрещён конфигом. Для `wf_grid` scope ограничен schema/pass-through совместимостью новых diagnostics, если соответствующий слой использует whitelist.

**Термины**

Разделить:

```text
wakeup-cycle active
FSM state
open/flat position
```

В Mode D:

```text
ST_ACTIVE_FREEZE = active wakeup-cycle
ST_STOPPING = wakeup-cycle already ended, position may still be held
```

`ST_STOPPING` после `exit.action.mode=block_new_entries`:

```text
new entries blocked
no reversals
position held until stopping close condition
not wakeup-cycle active
```

`lifecycle.stopping_exit = "opposite_st_flip"` остаётся без изменений. Это lifecycle-механика `ST_STOPPING`, не wakeup exit reason.

**Bar Pipeline Contract**

На каждом баре порядок Mode D обработки должен быть таким:

```text
1. capture bar-start snapshots
2. detect reset / ST flip / candidate primitives
3. reset handling, highest priority
4. OFF -> wakeup entry, only if not reset
5. update wakeup counters using PRE-internal-flip wakeup_active_direction
5b. write wakeup runtime diagnostics using post-entry, pre-Exit-C state:
    wakeup_regime_active
    wakeup_cycle_age_bars
    wakeup_bars_since_fresh_candidate
6. evaluate ttl / no_fresh_candidate Exit C
7. if Exit C did not fire, handle internal ST flip position action
8. handle ST_STOPPING close by opposite ST flip
9. write event/action diagnostics:
    wakeup_exit_reason
    wakeup_position_action
    wakeup_active_direction
10. write filtered_positions[t+1]
```

Runtime diagnostics are written before Exit C mutates state to `ST_STOPPING`/`OFF`.

Event/action diagnostics are written after event resolution.

Текущий порядок, где `wakeup_opposite_flip` проверяется до `ttl/no_fresh_candidate`, должен быть изменён. Exit C проверяется до internal ST flip.

**Predicates**

Использовать разные предикаты:

```python
wakeup_cycle_active_at_start = (
    state_at_bar_start == ZigZagFSMState.ST_ACTIVE_FREEZE
)

wakeup_cycle_active_for_runtime_diagnostics = (
    state == ZigZagFSMState.ST_ACTIVE_FREEZE
)
```

Для `ttl/no_fresh/internal ST flip` использовать `wakeup_cycle_active_at_start`, чтобы не было same-bar:

```text
entry -> exit
entry -> internal flip
```

Для wakeup-entry bar runtime diagnostics использовать post-entry, pre-Exit-C состояние, чтобы trigger bar получил:

```text
wakeup_regime_active = 1
wakeup_cycle_age_bars = 0
```

`ST_STOPPING` не входит ни в один active wakeup predicate.

Counter update guard должен быть только:

```python
state_at_bar_start == ZigZagFSMState.ST_ACTIVE_FREEZE
```

`ST_STOPPING` не должен инкрементировать:

```text
wakeup_cycle_age
wakeup_bars_since_fresh
```

**Event Priority**

Если на одном баре совпали события:

```text
reset
ttl
no_fresh_candidate
ST flip position action
```

Приоритет строго такой же.

Если `ttl/no_fresh_candidate` совпал с ST flip, применяется только Exit C:

```text
wakeup_position_action = exit_ttl
```

или:

```text
wakeup_position_action = exit_no_fresh_candidate
```

Internal ST flip на этом баре не применяется и не пишется.

**Internal ST Flip Contract**

Internal ST flip:

```text
wakeup_cycle_active_at_start == true
flip_dir != 0
not reset
not wakeup_exit_c_triggered_this_bar
```

Не определять internal ST flip через “opposite относительно held_pos”, потому что в `long/short` позиция может быть flat внутри active wakeup-cycle.

`trade_mode=revers/both`:

```text
held_pos = flip_dir
wakeup_active_direction = flip_dir
state = ST_ACTIVE_FREEZE
wakeup runtime continues
wakeup_exit_reason remains none
wakeup_position_action = reverse_on_st_flip
```

`trade_mode=long`:

```text
flip_dir == -1:
  held_pos = 0
  wakeup_position_action = flat_on_disallowed_st_flip

flip_dir == +1:
  held_pos = +1
  wakeup_position_action = restore_allowed_position_on_st_flip

wakeup_active_direction remains +1
state remains ST_ACTIVE_FREEZE
runtime continues
wakeup_exit_reason remains none
```

`trade_mode=short`:

```text
flip_dir == +1:
  held_pos = 0
  wakeup_position_action = flat_on_disallowed_st_flip

flip_dir == -1:
  held_pos = -1
  wakeup_position_action = restore_allowed_position_on_st_flip

wakeup_active_direction remains -1
state remains ST_ACTIVE_FREEZE
runtime continues
wakeup_exit_reason remains none
```

Flat из-за disallowed flip не означает `OFF` и не завершает wakeup-cycle. Следующий allowed flip восстанавливает `held_pos`, позиция отражается через `filtered_positions[t+1]`.

Existing non-Mode-D held position update guard остаётся:

```python
and not mode_d_enabled
```

Mode D `held_pos` update выполняется только в новом internal wakeup ST flip handler. Двойного update быть не должно.

**Wakeup Active Direction**

Добавить публичную диагностику:

```text
wakeup_active_direction
```

Смысл: направление активного wakeup-режима, не обязательно текущая открытая позиция.

Значения:

```text
0  = no active wakeup direction
+1 = long wakeup direction
-1 = short wakeup direction
```

Контракт:

```text
revers/both:
  on internal flip -> wakeup_active_direction = flip_dir

long:
  wakeup_active_direction = +1 throughout active wakeup-cycle

short:
  wakeup_active_direction = -1 throughout active wakeup-cycle
```

`wakeup_active_direction` используется для `no_fresh_candidate`.

`wakeup_active_direction_arr[t]` пишется после event resolution for this bar:

```text
active wakeup bar -> current active direction
Exit C bar -> last active direction
reset active wakeup bar -> 0
ST_STOPPING bars after Exit C -> 0
OFF bars -> 0
```

**Exit C**

Exit C срабатывает только по:

```text
ttl
no_fresh_candidate
```

При `exit.action.mode=close_position`:

```text
ttl/no_fresh -> close position -> OFF
wakeup-cycle ended
```

При `exit.action.mode=block_new_entries`:

```text
ttl/no_fresh -> ST_STOPPING
wakeup-cycle ended
position held until stopping close condition
```

На самом Exit C баре писать:

```text
wakeup_regime_active = 1
wakeup_cycle_age_bars = last actual age
wakeup_bars_since_fresh_candidate = last actual value
wakeup_active_direction = last active direction
wakeup_exit_reason = ttl / no_fresh_candidate
wakeup_position_action = exit_ttl / exit_no_fresh_candidate
```

На следующих `ST_STOPPING`/`OFF` барах не писать runtime diagnostics; defaults остаются:

```text
wakeup_regime_active = 0
wakeup_cycle_age_bars = -1
wakeup_bars_since_fresh_candidate = -1
wakeup_active_direction = 0
```

**Reset**

Reset имеет высший приоритет.

Если reset пришёл во время active wakeup-cycle:

```text
close position
state = OFF
wakeup runtime off
wakeup_exit_reason = reset
wakeup_position_action = exit_reset
wakeup_regime_active = 0
wakeup_cycle_age_bars = -1
wakeup_bars_since_fresh_candidate = -1
wakeup_active_direction = 0
```

Если reset пришёл в Mode D, но wakeup-cycle уже не active, например в `ST_STOPPING`:

```text
close position
state = OFF
wakeup_exit_reason = none
wakeup_position_action = none
wakeup_active_direction = 0
```

**ST_STOPPING**

После `ttl/no_fresh_candidate` с `exit.action.mode=block_new_entries`:

```text
state = ST_STOPPING
wakeup-cycle inactive
no new entries
no reversals
```

Если потом приходит opposite ST flip относительно текущей позиции:

```text
position closes
state -> OFF
```

Это не wakeup exit. Запрещено писать:

```text
wakeup_exit_reason = opposite_st_flip
```

Trade-level reason должен оставаться:

```text
filter_stopping_opposite_flip
```

**Diagnostics**

Добавить обязательные массивы:

```text
wakeup_position_action_arr:
  dtype=object
  default="none"

wakeup_active_direction_arr:
  dtype=np.int8
  default=0
```

Добавить в `filter_diagnostics_out`:

```text
wakeup_position_action
wakeup_active_direction
```

`wakeup_position_action` allowed values:

```text
none
reverse_on_st_flip
flat_on_disallowed_st_flip
restore_allowed_position_on_st_flip
exit_ttl
exit_no_fresh_candidate
exit_reset
```

`wakeup_exit_reason` allowed values после фикса:

```text
none
ttl
no_fresh_candidate
reset
```

`opposite_st_flip` запрещён как `wakeup_exit_reason` в Mode D.

**Required Code Changes**

В `donor/supertrend_optimizer/core/zigzag_st_filter.py`:

```text
1. Добавить wakeup_position_action_arr в _init_apply_arrays.
2. Добавить wakeup_active_direction_arr в _init_apply_arrays.
3. Добавить оба diagnostics в _finalize_apply_result.
4. Заменить wakeup_opposite_flip_this_bar на wakeup_internal_st_flip_this_bar.
5. Перенести ttl/no_fresh block выше internal ST flip handler.
6. Runtime diagnostics write оставить/разместить до Exit C state mutation.
7. Event/action diagnostics write выполнять после event resolution.
8. Counter update guard ограничить только ST_ACTIVE_FREEZE; убрать ST_STOPPING.
9. Оставить non-Mode-D Step 6 guard `and not mode_d_enabled`.
10. Mode D held_pos update делать только в новом internal flip handler.
11. Убрать wakeup_exit_reason_arr[t] = "opposite_st_flip" из active wakeup block.
12. Убрать wakeup_exit_reason_arr[t] = "opposite_st_flip" из ST_STOPPING close branch.
13. В ST_STOPPING close branch оставить закрытие позиции/FSM без wakeup exit reason.
14. Не менять lifecycle.stopping_exit config contract.
```

В `donor/supertrend_optimizer/core/filter_trade_diagnostics.py`:

```text
1. Читать wakeup_position_action.
2. Добавить trade-level mapping для internal wakeup actions.
3. Сохранить существующий ST_STOPPING fallback:
   state == ST_STOPPING -> filter_stopping_opposite_flip.
```

В tester summary / Excel:

```text
1. Добавить counters по wakeup_position_action.
2. Добавить display names для новых diagnostics.
3. Сохранить Wakeup Exit Opposite ST Flip как deprecated compatibility field со значением 0.
```

Для `wf_grid`:

```text
1. Проверить whitelist/pass-through diagnostics слои.
2. Если новые diagnostics требуют явного whitelist, добавить:
   wakeup_position_action
   wakeup_active_direction
3. Не включать Mode D pipeline support.
```

**Trade-Level Diagnostics**

`attach_trade_filter_diagnostics` должен читать:

```text
wakeup_exit_reason
wakeup_position_action
```

Приоритет `exit_reason`:

```text
1. wakeup_exit_reason: ttl/no_fresh/reset
2. wakeup_position_action: reverse_on_st_flip / flat_on_disallowed_st_flip
3. daily/time reset fallbacks
4. volume / exit B / ST_STOPPING fallbacks
5. st_flip
```

Mapping:

```text
ttl -> wakeup_exit_ttl
no_fresh_candidate -> wakeup_exit_no_fresh_candidate
reset -> wakeup_exit_reset
reverse_on_st_flip -> wakeup_reverse_on_st_flip
flat_on_disallowed_st_flip -> wakeup_flat_on_disallowed_st_flip
ST_STOPPING close -> filter_stopping_opposite_flip
```

`restore_allowed_position_on_st_flip` is not an exit reason.

If `restore_allowed_position_on_st_flip` is observed on an `exit_signal_idx` due to extractor edge cases:

```text
exit_reason = st_flip
```

`wakeup_exit_opposite_st_flip` больше не используется для Mode D internal reversals.

**Summary / Excel**

В summary оставить для совместимости:

```text
wakeup_exit_opposite_st_flip_count = 0
```

Добавить counters:

```text
wakeup_reverse_on_st_flip_count
wakeup_flat_on_disallowed_st_flip_count
wakeup_restore_allowed_position_on_st_flip_count
```

Считать их по `wakeup_position_action`.

В Excel diagnostics добавить display names:

```text
Wakeup Position Action
Wakeup Active Direction
```

В filters_summary добавить новые counters:

```text
Wakeup Reverse On ST Flip
Wakeup Flat On Disallowed ST Flip
Wakeup Restore Allowed Position On ST Flip
```

Сохранить:

```text
Wakeup Exit Opposite ST Flip
```

как deprecated compatibility field. В Mode D после фикса значение должно быть `0`. Не удалять это поле из Excel в рамках этого ТЗ.

**Tests**

Переписать legacy tests, которые ожидали `opposite_st_flip` как wakeup exit.

Обязательные группы:

```text
revers/both:
  internal ST flip reverses held_pos
  cycle remains active
  age does not reset
  exit_reason remains none
  action=reverse_on_st_flip
  active_direction updates to flip_dir
  ttl/no_fresh after reversals works
  same-bar ttl/no_fresh beats ST flip

long:
  disallowed short flip -> held_pos=0
  cycle remains active
  action=flat_on_disallowed_st_flip
  active_direction remains +1
  next long flip restores held_pos=+1
  action=restore_allowed_position_on_st_flip
  ttl/no_fresh after flat works

short:
  disallowed long flip -> held_pos=0
  cycle remains active
  action=flat_on_disallowed_st_flip
  active_direction remains -1
  next short flip restores held_pos=-1
  action=restore_allowed_position_on_st_flip
  ttl/no_fresh after flat works

reset:
  active wakeup reset writes reset/exit_reset and OFF
  reset in ST_STOPPING closes FSM/position
  reset in ST_STOPPING does not write wakeup reset

ST_STOPPING:
  block_new_entries Exit C -> ST_STOPPING
  ST_STOPPING is not wakeup active
  ST_STOPPING does not increment wakeup counters
  opposite flip closes position
  no wakeup_exit_reason=opposite_st_flip
  trade exit_reason=filter_stopping_opposite_flip

diagnostics:
  wakeup_position_action exists and has expected dtype/default
  wakeup_active_direction exists and has expected dtype/default
  valid wakeup_exit_reason values are subset of:
    none
    ttl
    no_fresh_candidate
    reset
  opposite_st_flip never appears in wakeup_exit_reason

summary/excel:
  new columns exist
  new counters exist
  Wakeup Exit Opposite ST Flip is present as compatibility field and equals 0
  trades sheet has no wakeup_exit_opposite_st_flip for internal reversals

legacy:
  Modes A/B/C/A+B/C+B unchanged
  Exit A/B unchanged
  non-Mode-D ST_STOPPING unchanged
  lifecycle.stopping_exit config unchanged
  wf_grid Mode D support status unchanged
```

**Acceptance Criteria**

```text
ST flips inside active wakeup change/flat/restore position by trade_mode
wakeup-cycle survives internal ST flips
runtime continues through internal ST flips
ttl/no_fresh/reset are the only wakeup-cycle terminators
Exit C bar keeps runtime diagnostics explaining the exit
ST_STOPPING is never wakeup-cycle active
ST_STOPPING does not increment wakeup counters
opposite_st_flip is not a Mode D wakeup_exit_reason
summary/excel separate wakeup exits from wakeup position actions
trade diagnostics distinguish internal wakeup actions from wakeup exits
legacy non-Mode-D behavior remains unchanged
```
