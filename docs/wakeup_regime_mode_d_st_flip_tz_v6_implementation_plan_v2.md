# Implementation Plan v2: Mode D Wakeup ST Flip

## Цель

Реализовать Mode D `wakeup_regime` как активный wakeup-cycle, а не как
одноразовый вход.

Базовый контракт:

```text
wakeup entry -> open position
ST flip внутри active wakeup-cycle -> только position action
wakeup-cycle остаётся active
ttl / no_fresh_candidate / reset -> завершает wakeup-cycle
```

`opposite_st_flip` больше не должен записываться в `wakeup_exit_reason` для
Mode D. При этом `opposite_st_flip` остаётся валидным lifecycle-механизмом
закрытия позиции в `ST_STOPPING`.

Документ является самостоятельным планом реализации.

## Термины и инварианты

В Mode D необходимо строго разделить:

```text
wakeup-cycle active     = state == ST_ACTIVE_FREEZE
FSM state               = OFF / WAIT / ST_ACTIVE_FREEZE / ST_STOPPING / ...
open position           = held_pos / filtered_positions
wakeup active direction = логическое направление active wakeup-cycle
```

Семантика FSM для Mode D:

```text
ST_ACTIVE_FREEZE = active wakeup-cycle
ST_STOPPING      = wakeup-cycle уже завершён, позиция может ещё удерживаться
OFF              = active wakeup-cycle отсутствует
```

`ST_STOPPING` после `exit.action.mode=block_new_entries`:

```text
new entries blocked
reversals blocked
position held until stopping close condition
not active wakeup-cycle
does not increment wakeup runtime counters
```

## Публичный diagnostics contract

Добавить два Mode D diagnostics массива.

```text
wakeup_position_action
  dtype=object
  default="none"
  allowed values:
    none
    reverse_on_st_flip
    flat_on_disallowed_st_flip
    restore_allowed_position_on_st_flip
    exit_ttl
    exit_no_fresh_candidate
    exit_reset

wakeup_active_direction
  dtype=np.int8
  default=0
  allowed values:
    -1
     0
    +1
```

Смысл полей:

```text
wakeup_exit_reason     = причина завершения wakeup-cycle
wakeup_position_action = действие с позицией / action на текущем баре
```

После реализации Mode D `wakeup_exit_reason` может содержать только:

```text
none
ttl
no_fresh_candidate
reset
```

`opposite_st_flip` запрещён как Mode D `wakeup_exit_reason`.

## Bar pipeline contract

Порядок обработки Mode D на каждом баре:

```text
1. Захватить immutable bar-start snapshots:
   state_at_bar_start
   held_pos_at_bar_start
   confirmed_legs_at_bar_start

2. Детектировать primitives:
   reset
   ST flip direction
   candidate primitives
   wakeup entry evaluation

3. Обработать reset с максимальным приоритетом.

4. Выполнить OFF -> wakeup entry, если:
   state_at_bar_start == OFF
   not reset
   time filter allows entry
   wakeup entry evaluation passes

5. Обновить wakeup runtime counters только если:
   state_at_bar_start == ST_ACTIVE_FREEZE

6. Записать runtime diagnostics по post-entry, pre-Exit-C состоянию:
   wakeup_regime_active
   wakeup_cycle_age_bars
   wakeup_bars_since_fresh_candidate

7. Проверить Exit C:
   ttl first
   no_fresh_candidate second

8. Если Exit C не сработал, обработать internal ST flip position action.

9. Обработать ST_STOPPING close by opposite ST flip.

10. Записать event/action diagnostics:
    wakeup_exit_reason
    wakeup_position_action
    wakeup_active_direction

11. Записать filtered_positions[t + 1].
```

Runtime diagnostics пишутся до того, как Exit C мутирует state в `ST_STOPPING`
или `OFF`.

Event/action diagnostics пишутся после разрешения всех событий на баре.

Реализационное уточнение для текущего `apply()`: фактический код сейчас
разорван на wakeup-блок и более позднюю ветку `ST_STOPPING` close внутри
записи `filtered_positions[t + 1]`. В новой схеме поздняя ветка
`ST_STOPPING` close не пишет wakeup diagnostics вообще. Она только закрывает
позицию/FSM. Поэтому единая запись `wakeup_exit_reason`,
`wakeup_position_action`, `wakeup_active_direction` остаётся внутри Mode D
wakeup event-resolution блока, а ниже по циклу эти массивы больше не трогаются.

Использовать явные predicates:

```python
wakeup_cycle_active_at_start = (
    state_at_bar_start == ZigZagFSMState.ST_ACTIVE_FREEZE
)

wakeup_cycle_active_for_runtime_diagnostics = (
    state == ZigZagFSMState.ST_ACTIVE_FREEZE
)
```

`ST_STOPPING` не должен входить ни в один active wakeup predicate.

## Event priority

Если на одном баре совпали события Mode D, приоритет строго такой:

```text
reset
ttl
no_fresh_candidate
internal ST flip position action
```

Same-bar `ttl/no_fresh_candidate + ST flip` должен дать только Exit C:

```text
wakeup_exit_reason = ttl / no_fresh_candidate
wakeup_position_action = exit_ttl / exit_no_fresh_candidate
```

Internal ST flip на этом баре не применяется и не записывается.

Same-bar wakeup entry не может сразу сделать Exit C и не может сразу обработать
internal ST flip. Для Exit C и internal ST flip требуется:

```text
state_at_bar_start == ST_ACTIVE_FREEZE
```

На баре internal ST flip `no_fresh_candidate` оценивается по pre-flip
`wakeup_active_direction`. Новое active direction после internal flip действует
для следующих баров. Это защищает от off-by-one в `no_fresh` на reversal-баре.

## Internal ST flip contract

Internal ST flip predicate:

```text
wakeup_cycle_active_at_start == true
flip_dir != 0
not reset
not wakeup_exit_c_triggered_this_bar
```

Нельзя определять internal ST flip через `opposite` относительно `held_pos`.
В `long` и `short` позиция может быть flat внутри active wakeup-cycle.

Поведение по `trade_mode`:

```text
trade_mode=revers/both:
  held_pos = flip_dir
  wakeup_active_direction = flip_dir
  wakeup_position_action = reverse_on_st_flip
  state remains ST_ACTIVE_FREEZE
  wakeup_exit_reason remains none

trade_mode=long:
  flip_dir == -1:
    held_pos = 0
    wakeup_active_direction = +1
    wakeup_position_action = flat_on_disallowed_st_flip

  flip_dir == +1:
    held_pos = +1
    wakeup_active_direction = +1
    wakeup_position_action = restore_allowed_position_on_st_flip

  state remains ST_ACTIVE_FREEZE
  wakeup_exit_reason remains none

trade_mode=short:
  flip_dir == +1:
    held_pos = 0
    wakeup_active_direction = -1
    wakeup_position_action = flat_on_disallowed_st_flip

  flip_dir == -1:
    held_pos = -1
    wakeup_active_direction = -1
    wakeup_position_action = restore_allowed_position_on_st_flip

  state remains ST_ACTIVE_FREEZE
  wakeup_exit_reason remains none
```

`wakeup_active_direction` является отдельным runtime state. Его нельзя
восстанавливать из `held_pos` после entry. Flat-позиция внутри active
wakeup-cycle всё равно имеет non-zero active direction.

## Exit C contract

Exit C terminators:

```text
ttl
no_fresh_candidate
```

Для `exit.action.mode=close_position`:

```text
state = OFF
held_pos = 0
wakeup-cycle ended
wakeup_exit_reason = ttl / no_fresh_candidate
wakeup_position_action = exit_ttl / exit_no_fresh_candidate
wakeup_exit_close_triggered = 1
```

Для `exit.action.mode=block_new_entries`:

```text
state = ST_STOPPING
position remains held
wakeup-cycle ended
wakeup_exit_reason = ttl / no_fresh_candidate
wakeup_position_action = exit_ttl / exit_no_fresh_candidate
wakeup_exit_close_triggered = 0
```

На самом Exit C баре runtime diagnostics должны объяснять active wakeup, из
которого произошёл exit:

```text
wakeup_regime_active = 1
wakeup_cycle_age_bars = last active age
wakeup_bars_since_fresh_candidate = last active value
wakeup_active_direction = last active direction
```

На следующих `ST_STOPPING` или `OFF` барах:

```text
wakeup_regime_active = 0
wakeup_cycle_age_bars = -1
wakeup_bars_since_fresh_candidate = -1
wakeup_active_direction = 0
```

## Reset contract

Reset имеет максимальный приоритет.

Reset во время active wakeup-cycle:

```text
state_at_bar_start == ST_ACTIVE_FREEZE
close position
state = OFF
held_pos = 0
wakeup_exit_reason = reset
wakeup_position_action = exit_reset
wakeup_regime_active = 0
wakeup_cycle_age_bars = -1
wakeup_bars_since_fresh_candidate = -1
wakeup_active_direction = 0
```

Reset в Mode D, когда wakeup-cycle уже не active, включая `ST_STOPPING`:

```text
close position
state = OFF
held_pos = 0
wakeup_exit_reason = none
wakeup_position_action = none
wakeup_active_direction = 0
```

Trade-level reason для reset в inactive wakeup / `ST_STOPPING` должен идти через
daily/time reset fallback, а не через wakeup reset.

## ST_STOPPING contract

`ST_STOPPING` не является active wakeup.

В Mode D `ST_STOPPING`:

```text
do not increment wakeup_cycle_age
do not increment wakeup_bars_since_fresh_candidate
do not write wakeup_regime_active = 1
do not write wakeup_exit_reason = opposite_st_flip
do not apply internal ST flip action
```

Если пришёл opposite ST flip относительно текущей удерживаемой позиции:

```text
position closes
state -> OFF
wakeup_exit_reason remains none
wakeup_position_action remains none
```

Trade-level reason для такого закрытия должен остаться:

```text
filter_stopping_opposite_flip
```

Граница последнего бара: текущая ветка close по `ST_STOPPING` opposite flip
выполняется только если есть `t + 1`. На последнем баре закрытие через эту
ветку недостижимо, и trade-level attribution должна опираться на существующий
fallback `pending_open_trade_at_end`. Этот edge-case не исправляется в рамках
Mode D ST flip изменения, но должен быть зафиксирован тестом.

Важная реализационная деталь: на close bar FSM может записать финальный
`trade_filter_state[t] = OFF`. Поэтому trade diagnostics должны смотреть не
только `trade_filter_state[t]`, но и `state_at_bar_start[t]`.

Если:

```text
state_at_bar_start[exit_signal_idx] == ST_STOPPING
```

и нет более приоритетного reset / wakeup exit reason, trade exit должен
классифицироваться как:

```text
filter_stopping_opposite_flip
```

## Core implementation steps

### 1. Baseline

До изменений прогнать focused baseline:

```bash
python -m pytest wf_grid/tests/test_wakeup_mode_d_entry.py -q
python -m pytest wf_grid/tests/test_wp5_zigzag_fsm.py wf_grid/tests/test_wp7_backtest_integration.py wf_grid/tests/test_wp9_diagnostics_export.py -q
python -m pytest wf_grid/tests/test_pr5_schema_contract.py wf_grid/tests/test_pr6_excel_contract.py -q
python -m pytest "donor TESTER/tests/test_phase2_wp_t7_excel_export.py" -q
```

Ожидаемо часть текущих Mode D тестов фиксирует legacy-поведение с
`opposite_st_flip`; эти ожидания нужно обновлять осознанно.

### 1b. Зафиксировать real-data characterization

Это изменение меняет торговое поведение Mode D, а не только diagnostics:
`opposite_st_flip` внутри `ST_ACTIVE_FREEZE` перестаёт закрывать cycle и для
`revers/both` становится reverse action. Поэтому до и после реализации нужно
прогнать tester на реальных Mode D конфигах и сохранить сравнение метрик.

Минимальный набор:

```text
config_tester_wakeup_block_new_entries.yml
config_tester_wakeup_close_position.yml, если файл присутствует в workspace
```

Сравнить до/после:

```text
num_trades
sum_pnl_pct / total return
max_drawdown
win_rate
exit_reason distribution
wakeup_position_action distribution
wakeup_exit_reason distribution
wakeup_exit_opposite_st_flip_count
```

Ожидание:

```text
legacy non-Mode-D результаты не меняются
Mode D результаты меняются объяснимо:
  active internal ST flips больше не завершают wakeup-cycle
  reverse/flat/restore actions появляются в diagnostics
  wakeup_exit_opposite_st_flip_count становится 0 для нового core output
```

### 2. Добавить diagnostics arrays

В `donor/supertrend_optimizer/core/zigzag_st_filter.py`:

```text
_init_apply_arrays:
  add wakeup_position_action_arr, dtype=object, default="none"
  add wakeup_active_direction_arr, dtype=np.int8, default=0

_finalize_apply_result:
  include wakeup_position_action for Mode D output
  include wakeup_active_direction for Mode D output
```

Не полагаться на локальный `filter_diagnostics_out` в конце `apply()`.
Фактический return идёт через `_finalize_apply_result(...)`.

### 3. Перестроить Mode D event resolution

В основном цикле `apply()` завести локальные переменные события на бар:

```text
wakeup_exit_reason_this_bar = "none"
wakeup_position_action_this_bar = "none"
wakeup_exit_c_triggered_this_bar = False
wakeup_internal_st_flip_this_bar = False
wakeup_active_direction_for_diag = 0
```

Писать:

```text
wakeup_exit_reason_arr[t]
wakeup_position_action_arr[t]
wakeup_active_direction_arr[t]
```

один раз после Mode D event resolution.

Runtime diagnostics оставить до Exit C state mutation.

Из-за текущей структуры `apply()` не переносить поздний `ST_STOPPING` close в
wakeup diagnostics. После Mode D event-resolution wakeup diagnostics считаются
финальными для бара. Ветка `ST_STOPPING` close ниже по циклу не должна писать
`wakeup_exit_reason`, `wakeup_position_action` или `wakeup_active_direction`.

### 3b. Исправить reset guard

В reset-блоке `apply()` сузить Mode D wakeup reset attribution:

```text
было:
  state_at_bar_start in (ST_ACTIVE_FREEZE, ST_STOPPING)

стало:
  state_at_bar_start == ST_ACTIVE_FREEZE
```

Только active wakeup reset пишет:

```text
wakeup_exit_reason = reset
wakeup_position_action = exit_reset
```

Reset в `ST_STOPPING` закрывает позицию/FSM, но не пишет wakeup reset. На
trade-level он классифицируется через daily/time reset fallback.

### 4. Исправить counters и runtime diagnostics

Заменить active predicate:

```text
было: state in (ST_ACTIVE_FREEZE, ST_STOPPING)
стало: state == ST_ACTIVE_FREEZE
```

Counter update guard:

```text
state_at_bar_start == ST_ACTIVE_FREEZE
```

Entry bar:

```text
wakeup_cycle_age = 0
wakeup_active_direction = candidate direction
wakeup_regime_active = 1
```

Exit C bar:

```text
runtime diagnostics remain active for that bar
event diagnostics record exit reason/action
state may become OFF or ST_STOPPING after runtime write
```

Также убрать текущую связку:

```text
wakeup_active_direction = held_pos
```

Active direction задаётся только:

```text
на wakeup entry
в internal ST flip handler
при OFF/reset runtime clear -> 0
```

Это обязательно для `long/short`, где active wakeup-cycle может быть flat.

### 5. Добавить helper для internal ST flip

Добавить маленький helper, например:

```text
_apply_mode_d_internal_st_flip(
    held_pos,
    wakeup_active_direction,
    flip_dir,
    trade_mode,
) -> result
```

Helper должен вернуть:

```text
new held_pos
new wakeup_active_direction
wakeup_position_action
```

Helper не должен менять `wakeup_exit_reason`.

### 6. Удалить Mode D opposite flip wakeup exit

Убрать Mode D записи:

```text
wakeup_exit_reason_arr[t] = "opposite_st_flip"
```

из:

```text
active wakeup block
ST_STOPPING close branch
```

Сохранить lifecycle-поведение:

```text
ST_STOPPING closes position on opposite ST flip
lifecycle.stopping_exit = "opposite_st_flip" remains valid config
```

### 7. Сохранить non-Mode-D behavior

Оставить non-Mode-D ST flip position update guard:

```text
and not mode_d_enabled
```

Не менять Modes A, B, C, A+B, C+B, кроме безопасной pass-through
совместимости diagnostics.

Non-Mode-D output не должен получать Mode D wakeup diagnostics, если текущая
архитектура уже условно отдаёт wakeup diagnostics только для Mode D.

### 8. Точный список код-сайтов

Минимальные core code sites:

```text
donor/supertrend_optimizer/core/zigzag_st_filter.py

reset guard:
  текущий блок около combined_reset_event:
  убрать ST_STOPPING из wakeup reset attribution

wakeup active-direction bootstrap:
  убрать присваивание wakeup_active_direction = held_pos

Mode D active wakeup opposite flip:
  заменить закрытие cycle по opposite_st_flip на internal ST flip action

Exit C block:
  выполнить раньше internal ST flip
  писать exit_ttl / exit_no_fresh_candidate в wakeup_position_action

ST_STOPPING close branch:
  закрывать позицию/FSM
  не писать wakeup_exit_reason=opposite_st_flip
  не писать wakeup_position_action

filtered_positions[t + 1]:
  проверить flat-in-FREEZE:
  state == ST_ACTIVE_FREEZE и held_pos == 0 должны сохранять active cycle
```

## Trade-level diagnostics

В `donor/supertrend_optimizer/core/filter_trade_diagnostics.py` читать:

```text
wakeup_exit_reason
wakeup_position_action
state_at_bar_start
```

Нормативный порядок ниже является уточнением для реализации. Он намеренно
ставит reset fallbacks выше `wakeup_position_action`, чтобы action не мог
перебить более приоритетный reset или Exit C reason.

Приоритет `exit_reason`:

```text
1. wakeup_exit_reason:
   ttl                -> wakeup_exit_ttl
   no_fresh_candidate -> wakeup_exit_no_fresh_candidate
   reset              -> wakeup_exit_reset

2. reset fallbacks:
   daily_reset_event       -> filter_daily_reset
   time_filter_reset_event -> filter_time_reset

3. pending open trade at end

4. exit B / volume / forced-exit fallbacks

5. wakeup_position_action:
   reverse_on_st_flip         -> wakeup_reverse_on_st_flip
   flat_on_disallowed_st_flip -> wakeup_flat_on_disallowed_st_flip
   restore_allowed_position_on_st_flip -> not an exit reason, fallback later

6. ST_STOPPING close:
   fsm_at_exit == ST_STOPPING
   or state_at_bar_start[exit_signal_idx] == ST_STOPPING
   -> filter_stopping_opposite_flip

7. default:
   st_flip
```

Не маппить `opposite_st_flip` из `wakeup_exit_reason` для нового Mode D output.
Допустимо оставить backward-compatible defensive mapping только для внешних
legacy diagnostics, но core Mode D больше не должен производить это значение.

Entry-side attribution для `restore_allowed_position_on_st_flip`: restore в
`long/short` открывает новую позицию из flat внутри active wakeup-cycle. В
рамках этой реализации entry diagnostics не получают новый отдельный
`entry_trigger_source`; вход остаётся атрибутирован через существующие
`entry_filter_state` / bar-level diagnostics. Если потребуется отдельная
trade-level entry reason для restore, это отдельный follow-up, не часть этого
изменения.

## Tester summary

В `donor/supertrend_optimizer/testing/runner.py` читать
`wakeup_position_action`.

Добавить counters:

```text
wakeup_reverse_on_st_flip_count
wakeup_flat_on_disallowed_st_flip_count
wakeup_restore_allowed_position_on_st_flip_count
```

Считать их по `wakeup_position_action`.

`wakeup_position_action` присутствует в диагностике только для Mode D (добавляется
под Mode-D гард в `_finalize_apply_result`). На non-Mode-D прогонах ключ отсутствует.
Три новых каунтера должны читаться защитно:

```python
action_arr = filter_diagnostics.get("wakeup_position_action")
wakeup_reverse_on_st_flip_count = (
    int(np.sum(action_arr == "reverse_on_st_flip")) if action_arr is not None else 0
)
# аналогично для flat_on_disallowed_st_flip и restore_allowed_position_on_st_flip
```

По аналогии с существующим `wakeup_exit_opposite_st_flip_count`, который уже
читает `exit_reason` через `.get()`.

Оставить compatibility field:

```text
wakeup_exit_opposite_st_flip_count
```

После реализации для core-generated Mode D diagnostics значение должно быть 0.

## Tester Excel

В `donor/supertrend_optimizer/io/excel_tester.py` добавить per-bar display
names:

```text
wakeup_position_action   -> Wakeup Position Action
wakeup_active_direction  -> Wakeup Active Direction
```

В `filters_summary` добавить:

```text
Wakeup Reverse On ST Flip
Wakeup Flat On Disallowed ST Flip
Wakeup Restore Allowed Position On ST Flip
```

Оставить:

```text
Wakeup Exit Opposite ST Flip
```

как deprecated compatibility field. Для Mode D после реализации оно должно
быть 0.

Обновить exact Excel/schema snapshots, которые намеренно ловят drift display
names и summary labels.

## WF Grid compatibility

Не включать поддержку Mode D в `wf_grid`, если текущий config gate запрещает
Mode D / `wakeup_regime`.

Ожидаемый scope для `wf_grid`:

```text
Mode D config remains rejected by wf_grid
per-bar retained diagnostics pass through new keys if present
diagnostics stripping remains behaviorally unchanged
summary/export schema snapshots updated only where required
```

Вероятные pass-through точки:

```text
wf_grid/wf/step_executor.py:
  filter_diagnostics_oos slices all keys generically

wf_grid/export/xlsx_writer.py:
  WF_FilterDiagnostics writes all retained diagnostic keys generically

wf_grid orchestration / multiprocessing helpers:
  strip logic nulls whole diagnostics object, not individual keys

wf_grid/wf/_mp_helpers.py:
  _strip_filter_diagnostics_arrays only nulls whole diagnostics object
```

Менять эти места только если тесты докажут наличие whitelist.

## Test plan

### Core Mode D tests

Добавить или обновить тесты в `wf_grid/tests/test_wakeup_mode_d_entry.py` либо
в новом focused Mode D файле.

Обязательные cases:

```text
revers/both:
  internal ST flip reverses held_pos
  wakeup-cycle remains active
  wakeup_cycle_age continues
  wakeup_exit_reason remains none
  wakeup_position_action = reverse_on_st_flip
  wakeup_active_direction updates to flip_dir
  ttl/no_fresh after internal reversals works

long:
  disallowed short flip -> held_pos=0
  wakeup-cycle remains active
  action=flat_on_disallowed_st_flip
  active_direction remains +1
  next long flip restores held_pos=+1
  action=restore_allowed_position_on_st_flip
  ttl/no_fresh works while flat
  filtered_positions[t + 1] can be 0 while state remains ST_ACTIVE_FREEZE

short:
  disallowed long flip -> held_pos=0
  wakeup-cycle remains active
  action=flat_on_disallowed_st_flip
  active_direction remains -1
  next short flip restores held_pos=-1
  action=restore_allowed_position_on_st_flip
  ttl/no_fresh works while flat
  filtered_positions[t + 1] can be 0 while state remains ST_ACTIVE_FREEZE
```

### Priority tests

```text
reset beats ttl/no_fresh/ST flip
ttl beats no_fresh_candidate
ttl beats same-bar ST flip
no_fresh_candidate beats same-bar ST flip
entry bar cannot same-bar exit
entry bar cannot same-bar internal ST flip
no_fresh on internal-flip bar uses pre-flip active_direction
```

### Exit C tests

```text
Exit C bar keeps runtime diagnostics active
Exit C close_position -> OFF and flat
Exit C block_new_entries -> ST_STOPPING and position held
following ST_STOPPING bars have runtime diagnostics off
```

### Reset tests

```text
active wakeup reset writes reset/exit_reset and OFF
reset in ST_STOPPING closes FSM/position
reset in ST_STOPPING does not write wakeup reset
reset in ST_STOPPING maps trade-level reason via daily/time reset fallback
```

### ST_STOPPING tests

```text
ST_STOPPING is not wakeup active
ST_STOPPING does not increment wakeup counters
ST_STOPPING does not write wakeup_active_direction
opposite flip closes position/FSM
opposite flip does not write wakeup_exit_reason=opposite_st_flip
trade exit_reason remains filter_stopping_opposite_flip
trade diagnostic uses state_at_bar_start when final state on close bar is OFF
last-bar ST_STOPPING opposite flip falls back to pending_open_trade_at_end
```

### Diagnostics tests

```text
wakeup_position_action exists in Mode D
wakeup_active_direction exists in Mode D
dtypes/defaults are correct
wakeup_position_action values are in allowed set
wakeup_active_direction values are in {-1, 0, +1}
wakeup_exit_reason values are subset of {none, ttl, no_fresh_candidate, reset}
opposite_st_flip never appears in Mode D wakeup_exit_reason
```

### Trade-level diagnostics tests

```text
ttl -> wakeup_exit_ttl
no_fresh_candidate -> wakeup_exit_no_fresh_candidate
reset -> wakeup_exit_reset
reverse_on_st_flip -> wakeup_reverse_on_st_flip
flat_on_disallowed_st_flip -> wakeup_flat_on_disallowed_st_flip
restore_allowed_position_on_st_flip -> st_flip fallback if observed at exit
ST_STOPPING close -> filter_stopping_opposite_flip
daily/time reset keeps higher priority than action fallback
Exit C + same-bar flip maps to Exit C reason, not action reason
restore_allowed_position_on_st_flip is not treated as exit reason
reset fallback is higher priority than wakeup_position_action
```

### Real-data characterization tests

```text
run tester before/after on config_tester_wakeup_block_new_entries.yml
run tester before/after on config_tester_wakeup_close_position.yml if present
save metrics/exits/action distributions in implementation notes
explain Mode D deltas caused by internal ST flip no longer terminating cycle
confirm wakeup_exit_opposite_st_flip_count == 0 after implementation
```

### Summary и Excel tests

```text
new counters exist
new counters are computed from wakeup_position_action
Wakeup Exit Opposite ST Flip remains present and equals 0 for new Mode D output
new per-bar display names exist
filters_summary includes new counters
exact schema/display-name snapshots are updated
```

### WF Grid compatibility tests

```text
wf_grid still rejects Mode D / wakeup_regime config
retained per-bar diagnostics can carry new keys through generic export
disabled/non-Mode-D schemas remain unchanged
```

### Legacy regression tests

```text
Modes A/B/C/A+B/C+B unchanged
Exit A/B unchanged
non-Mode-D ST_STOPPING unchanged
lifecycle.stopping_exit config unchanged
disabled filter path unchanged
volume/time-filter/daily-reset priority unchanged outside Mode D changes
```

## Оценка объёма

Это не быстрый diagnostics patch. Реалистичная оценка:

```text
core FSM / Mode D event-resolution: 1.5-2 дня
trade diagnostics + runner summary + Excel: 0.5-1 день
переписывание и добавление focused tests: 1-1.5 дня
real-data characterization + фиксация дельт: 0.5 дня
резерв на regressions в горячем loop: 0.5 дня
```

Итого: примерно 3-5 сфокусированных рабочих дней.

## Acceptance gate

После реализации прогнать:

```bash
python -m pytest wf_grid/tests/test_wakeup_mode_d_entry.py -q
python -m pytest wf_grid/tests/test_wp5_zigzag_fsm.py wf_grid/tests/test_wp7_backtest_integration.py wf_grid/tests/test_wp9_diagnostics_export.py -q
python -m pytest wf_grid/tests/test_pr5_schema_contract.py wf_grid/tests/test_pr6_excel_contract.py -q
python -m pytest wf_grid/tests/test_wp2_config_trade_filter.py -q
python -m pytest "donor TESTER/tests/test_phase2_wp_t7_excel_export.py" -q
```

Также выполнить real-data tester characterization:

```text
config_tester_wakeup_block_new_entries.yml
config_tester_wakeup_close_position.yml, если файл присутствует
```

В implementation notes приложить:

```text
before/after metrics
exit_reason distribution
wakeup_position_action distribution
wakeup_exit_reason distribution
объяснение всех Mode D дельт
```

Если есть время, прогнать broader non-slow suite:

```bash
python -m pytest wf_grid/tests -m "not slow" -q
```

Acceptance criteria:

```text
1. opposite_st_flip never appears in Mode D wakeup_exit_reason.
2. Internal ST flip inside active wakeup-cycle changes only position/action.
3. Internal ST flip does not end wakeup-cycle.
4. ttl/no_fresh/reset are the only wakeup-cycle terminators.
5. Exit C bar preserves runtime diagnostics.
6. ST_STOPPING is never active wakeup.
7. ST_STOPPING does not increment wakeup counters.
8. ST_STOPPING opposite flip close remains trade-level filter_stopping_opposite_flip.
9. Summary/Excel separate wakeup exits from wakeup position actions.
10. wf_grid Mode D support status remains unchanged.
11. Legacy non-Mode-D behavior remains unchanged.
12. Real-data Mode D deltas are measured and explained.
```

## Главные риски

```text
1. Потерять filter_stopping_opposite_flip после удаления
   wakeup_exit_reason=opposite_st_flip.
2. Рассинхронизировать state, position и diagnostics на event-heavy барах.
3. Сломать no_fresh в long/short, когда wakeup-cycle active, но позиция flat.
4. Уронить snapshot/schema тесты после добавления Excel display names.
5. Случайно изменить non-Mode-D FSM при переносе shared blocks.
6. Дать wakeup_position_action перебить более приоритетный reset или Exit C
   reason на trade-level.
7. Пропустить реальное изменение стратегии, если ограничиться unit tests.
```

Mitigation:

```text
use state_at_bar_start for ST_STOPPING trade attribution
write event diagnostics once per bar
keep wakeup_active_direction independent from held_pos
update schema snapshots intentionally
gate all Mode D behavior with mode_d_enabled
cover same-bar priority cases with explicit tests
run before/after real-data tester characterization
```
