# Implementation Plan v2: Tester-Only Wakeup Cycle Trade Limit

## 1. Цель

Добавить в tester opt-in лимит количества реальных открытий позиции внутри
одного Mode D `wakeup_regime`-цикла.

Фича активна только когда одновременно выполняются все условия:

```yaml
trade_filter:
  enabled: true
  zigzag:
    mode: D
  wakeup_regime:
    enabled: true
    exit:
      max_trades_per_cycle:
        enabled: true
        max_trades: 5
```

`max_trades = N` означает: в одном wakeup-цикле разрешено не более `N`
реальных открытий позиции, включая стартовый вход цикла. После достижения
лимита цикл завершается через существующий механизм Mode D Exit C и использует
общий `trade_filter.wakeup_regime.exit.action.mode`.

Лимит считается только внутри текущего wakeup-цикла. После фактического возврата
FSM в `OFF` новый цикл может стартовать только через полный wakeup entry-gate и
получает новый счетчик с `1`.

## 2. Не-цели

- Не менять режимы `A`, `B`, `C`, `A+B`, `C+B`.
- Не менять расчет `candidate_leg_direction`.
- Не добавлять поддержку `wakeup_regime` в runtime `wf_grid`.
- Не протаскивать `caller_pipeline` в `apply()`.
- Не менять open-to-open contract: решение на close `t`, исполнение на
  open `t+1`.
- Не менять поведение при отсутствующем блоке или `enabled: false`.
- Не добавлять per-flip suppression и не вводить action
  `blocked_cycle_trade_limit`.
- Не добавлять summary aggregate вроде
  `wakeup_exit_cycle_trade_limit_count` в рамках этой реализации.
- Не переносить код из `donor TESTER/supertrend_optimizer`: активный пакет для
  tester-тестов резолвится из `donor/supertrend_optimizer`.

## 3. Затрагиваемые модули

Config and validation:

```text
donor/supertrend_optimizer/core/trade_filter_config.py
wf_grid/config/loader.py
wf_grid/tests/test_wp2_config_trade_filter.py
```

Runtime:

```text
donor/supertrend_optimizer/core/zigzag_st_filter.py
wf_grid/tests/test_wakeup_mode_d_entry.py
```

Diagnostics, trade mapping, Excel/export:

```text
donor/supertrend_optimizer/core/filter_trade_diagnostics.py
donor/supertrend_optimizer/io/excel_tester.py
donor/supertrend_optimizer/testing/runner.py
wf_grid/tests/test_pr5_schema_contract.py
wf_grid/tests/test_xlsx_export.py
donor TESTER/tests/test_xlsx_cycle_sheet.py
```

## 4. Config Contract

### 4.1 YAML shape

```yaml
trade_filter:
  wakeup_regime:
    exit:
      max_trades_per_cycle:
        enabled: true
        max_trades: 5
      ttl:
        enabled: true
        bars: 90
      no_fresh_candidate:
        enabled: false
      action:
        mode: block_new_entries
```

`action.mode` остается общим для всех Exit C причин:

```text
block_new_entries
close_position
```

### 4.2 Dataclasses

Добавить:

```python
@dataclass
class TradeFilterWakeupMaxTradesPerCycleConfig:
    enabled: object = False
    max_trades: object = None
```

Расширить:

```python
@dataclass
class TradeFilterWakeupExitConfig:
    ttl: TradeFilterWakeupTtlExitConfig = field(
        default_factory=TradeFilterWakeupTtlExitConfig
    )
    no_fresh_candidate: TradeFilterWakeupNoFreshCandidateExitConfig = field(
        default_factory=TradeFilterWakeupNoFreshCandidateExitConfig
    )
    max_trades_per_cycle: TradeFilterWakeupMaxTradesPerCycleConfig = field(
        default_factory=TradeFilterWakeupMaxTradesPerCycleConfig
    )
    action: TradeFilterWakeupExitActionConfig = field(
        default_factory=TradeFilterWakeupExitActionConfig
    )
```

Raw values остаются `object` до validation. Не приводить строки/числа к bool.

### 4.3 Strict schema

В shared whitelist добавить:

```text
trade_filter.wakeup_regime.exit:
  ttl, no_fresh_candidate, max_trades_per_cycle, action

trade_filter.wakeup_regime.exit.max_trades_per_cycle:
  enabled, max_trades
```

В `wf_grid/config/loader.py` добавить такой же mirror. Это только recognition
schema: `wf_grid` все равно отвергает `wakeup_regime` как unsupported pipeline,
а не как unknown key.

### 4.4 Validation

В `_validate_wakeup_regime_block`:

- отсутствующий `max_trades_per_cycle` означает `enabled=False`,
  `max_trades=None`;
- `enabled`, если указан, должен быть strict `bool`;
- если `enabled is True`, `max_trades` обязателен и должен быть `int >= 1`;
- `0`, `2.5`, `"5"`, `None`, `True`, `False` для `max_trades` отвергаются,
  когда блок включен;
- unknown keys внутри `max_trades_per_cycle` отвергаются strict schema
  механизмом;
- `enabled=True` при `wakeup_regime.enabled=False` проходит type validation и
  является runtime no-op;
- `max_trades_per_cycle.enabled=True` считается enabled exit condition в правиле
  "at least one enabled exit condition";
- при `wakeup_regime.enabled=True` `exit.action.mode` остается обязательным,
  даже если единственный enabled exit condition - trade limit.

## 5. Runtime Predicate

В `apply()` вывести один runtime predicate и один runtime max value. Все runtime
решения и diagnostic config echo должны использовать именно их.

```python
wakeup_cycle_trade_limit_enabled = (
    mode_d_enabled
    and wakeup_regime_cfg is not None
    and getattr(wakeup_regime_cfg, "enabled", False) is True
    and max_trades_per_cycle_cfg is not None
    and getattr(max_trades_per_cycle_cfg, "enabled", False) is True
)
wakeup_cycle_trade_limit_max_trades = (
    int(getattr(max_trades_per_cycle_cfg, "max_trades"))
    if wakeup_cycle_trade_limit_enabled
    else 0
)
```

Runtime не должен зависеть от `caller_pipeline`. Tester-only ограничение
обеспечивается validation layer.

## 6. Runtime State And OFF Semantics

Добавить loop-local scalar:

```python
cycle_trade_count = 0
```

Инварианты:

```text
outside active-or-closing wakeup cycle: 0
wakeup start: 1
ST_ACTIVE_FREEZE: number of real openings in current wakeup cycle
ST_STOPPING after block_new_entries: preserved until actual OFF
actual OFF: 0
```

Важно: существующая логика `_wakeup_runtime_off()` сейчас сбрасывает wakeup
runtime, когда state уже не `ST_ACTIVE_FREEZE`. Нельзя привязывать reset
`cycle_trade_count` к этому условию, иначе `ST_STOPPING` потеряет счетчик.

Для нового счетчика ввести отдельное понятие:

```python
wakeup_cycle_live = state in (
    ZigZagFSMState.ST_ACTIVE_FREEZE,
    ZigZagFSMState.ST_STOPPING,
)
```

`cycle_trade_count` сбрасывается только в фактических переходах wakeup-cycle в
`OFF`, а не при переходе `ST_ACTIVE_FREEZE -> ST_STOPPING`.

### 6.1 OFF-only reset helper

Ввести helper или один локальный assignment block только для true-OFF sites.
Это не замена всем вызовам `_wakeup_runtime_off()`: существующий вызов при
выходе из `ST_ACTIVE_FREEZE` в `ST_STOPPING` остается отдельным и не должен
трогать `cycle_trade_count`.

```python
def _reset_wakeup_cycle_runtime_values_for_off():
    return {
        "cycle_direction": 0,
        "cycle_trade_count": 0,
        "pos_freeze_until": -1,
        "pos_freeze_pending": False,
        "wakeup_runtime": _wakeup_runtime_off(),
    }
```

На практике helper может возвращать tuple, потому что nested mutating helper с
Python scoping неудобен. Критичен не формат, а единая OFF-only операция reset.

Actual OFF sites, где счетчик обязан сбрасываться:

```text
1. init перед loop;
2. combined daily/time reset wipe;
3. Exit C action.mode=close_position;
4. ST_STOPPING + cur_pos == 0 normalization;
5. ST_STOPPING close on opposite ST flip;
6. любой будущий Mode D branch, который переводит wakeup-cycle в OFF.
```

Не сбрасывать `cycle_trade_count` в Exit C `block_new_entries`, потому что это
перевод в `ST_STOPPING`, а не фактический конец цикла.

Exit-B immediate-off не является reachable path для Mode D: Mode D стартует
сразу в `ST_ACTIVE_FREEZE` и не использует `ST_COUNTING_ZZ_LEGS`. Это допущение
зафиксировать кодовым guard-комментарием рядом с Exit-B immediate-off branch или
рядом с Mode D dispatcher. Если рядом уже есть удобный low-noise test seam,
можно добавить guard-test, но первичный guard должен быть в коде, чтобы будущий
рефактор FSM видел инвариант в месте изменения.

### 6.2 Full OFF fields

Каждый actual OFF переход должен явно приводить связанные поля к OFF invariant:

```text
state = OFF
confirmed_legs_since_start = -1
zz_legs_since_lifecycle_start = -1
held_pos = 0
cycle_direction = 0
cycle_trade_count = 0
pos_freeze_until = -1
pos_freeze_pending = False
wakeup_cycle_age, wakeup_bars_since_fresh, wakeup_active_direction,
  wakeup_exit_c_fired = _wakeup_runtime_off()
```

`_stopping_start` остается общей FSM diagnostic state и сбрасывается по
существующей логике, но если OFF переход уже сегодня явно сбрасывает
`_stopping_start`, новый код не должен это убрать.

## 7. Counting Rules

Счетчик работает всегда, независимо от `collect_filter_diagnostics`.

На старте wakeup-cycle:

```python
wakeup_started_this_bar = True
cycle_trade_count = 1
```

После internal ST flip handling и position-freeze release handling выполнить
ровно одно post-action increment rule:

```python
if (
    not wakeup_started_this_bar
    and state_at_bar_start == ZigZagFSMState.ST_ACTIVE_FREEZE
    and state == ZigZagFSMState.ST_ACTIVE_FREEZE
    and wakeup_position_action_this_bar
       in {"restore_allowed_position_on_st_flip", "reverse_on_st_flip"}
):
    cycle_trade_count += 1
```

Это покрывает обычные internal ST flips и release path, потому что release
записывает те же action strings.

Не считать открытиями:

```text
flat_on_disallowed_st_flip
position_freeze_ignored_opposite_st_flip
exit_ttl
exit_no_fresh_candidate
exit_cycle_trade_limit
exit_reset
none
```

Порядок на баре должен быть явным:

```text
1. internal ST flip / position-freeze release выставляют action scalar;
2. post-action rule инкрементит cycle_trade_count;
3. wakeup_cycle_trade_count_arr[t] записывает уже обновленный count;
4. wakeup_exit_reason_arr[t] и wakeup_position_action_arr[t] записывают
   scalar labels этого бара.
```

Это намеренно отличается от ранней записи `wakeup_cycle_age`: счетчик нельзя
писать до post-action increment, иначе на баре нового открытия diagnostics
будет отставать на 1.

## 8. Exit C Trigger

Расширить `_resolve_mode_d_wakeup_exit()` так, чтобы `max_trades_per_cycle`
возвращался вместе с остальными Exit C компонентами. Предпочтительно заменить
магический tuple на `NamedTuple`, например:

```python
class _WakeupExitConfigParts(NamedTuple):
    ttl: object
    no_fresh_candidate: object
    max_trades_per_cycle: object
    action: object
```

### 8.1 Reason/action mapping inside runtime

Добавить runtime helper:

```python
def _wakeup_exit_action_for_reason(reason: str) -> str:
    if reason == "ttl":
        return "exit_ttl"
    if reason == "no_fresh_candidate":
        return "exit_no_fresh_candidate"
    if reason == "cycle_trade_limit":
        return "exit_cycle_trade_limit"
    raise AssertionError(reason)
```

Exit C transition body должен быть один общий блок для `ttl`,
`no_fresh_candidate`, `cycle_trade_limit`.

### 8.2 Priority

Priority строго:

```text
ttl > no_fresh_candidate > cycle_trade_limit
```

Cycle limit condition:

```python
(
    wakeup_cycle_trade_limit_enabled
    and cycle_trade_count >= wakeup_cycle_trade_limit_max_trades
)
```

Проверка выполняется в существующем Mode D Exit C месте: после обновления
wakeup age/fresh counters текущего бара, но до internal ST flip handling и до
position-freeze release.

Гейты:

```text
state_at_bar_start == ST_ACTIVE_FREEZE
state == ST_ACTIVE_FREEZE
not wakeup_exit_c_fired
not is_reset
```

Так `N+1` opening не происходит: `N`-е открытие считается на decision bar `t`,
а на следующем active bar Exit C срабатывает до internal ST flip/release.

## 9. Action Mode Behavior

### 9.1 block_new_entries

При `exit.action.mode: block_new_entries`:

```text
state = ST_STOPPING
wakeup_exit_c_fired = True
wakeup_exit_c_triggered_this_bar = True
wakeup_exit_reason_this_bar = "cycle_trade_limit"
wakeup_position_action_this_bar = "exit_cycle_trade_limit"
cycle_trade_count preserved
```

Existing `ST_STOPPING` logic закрывает позицию на opposite ST flip или
нормализует в `OFF`, если позиции уже нет. Только actual OFF сбрасывает
`cycle_trade_count`.

### 9.2 close_position

При `exit.action.mode: close_position`:

```text
wakeup_exit_c_fired = True
wakeup_exit_c_triggered_this_bar = True
wakeup_exit_reason_this_bar = "cycle_trade_limit"
wakeup_position_action_this_bar = "exit_cycle_trade_limit"
wakeup_exit_close_triggered[t] = 1   # diag only
state = OFF
held_pos = 0
positions[t + 1] = 0 when t + 1 < n
actual OFF reset clears cycle_trade_count
```

На trigger bar diagnostic `wakeup_exit_cycle_trade_limit_triggered` обязан быть
`1`. `wakeup_cycle_trade_count` на этом же баре может быть `-1`, потому что FSM
уже в `OFF`; максимальное значение счетчика должно быть видно на предыдущем
active/closing участке цикла.

Если cycle limit сработал на баре, internal ST flip handling и freeze release
на этом баре не выполняются.

## 10. Diagnostics

Добавить три per-bar поля только для Mode D diagnostics output:

```text
wakeup_cycle_trade_count                 int64
wakeup_exit_cycle_trade_limit_triggered  int8
wakeup_cycle_trade_limit_config          int64
```

Diagnostic sentinel:

```text
runtime cycle_trade_count outside cycle = 0
diagnostic wakeup_cycle_trade_count in OFF = -1
diagnostic wakeup_cycle_trade_count in ST_ACTIVE_FREEZE/ST_STOPPING =
  current runtime cycle_trade_count
```

### 10.1 Allocation

`collect_filter_diagnostics=False` не вызывает `_allocate_apply_arrays`.

При `collect_filter_diagnostics=True` новые три массива аллоцировать
безусловно, как и существующие `wakeup_*` arrays. Это сохраняет текущий
паттерн `_allocate_apply_arrays`: массивы существуют в internal arrays dict для
любого mode, а наружу non-D поля отсекаются только в `_finalize_apply_result`.

```python
"wakeup_cycle_trade_count_arr": np.full(n, -1, dtype=np.int64)
"wakeup_exit_cycle_trade_limit_triggered_arr": np.zeros(n, dtype=np.int8)
"wakeup_cycle_trade_limit_config_arr": np.full(
    n,
    int(wakeup_cycle_trade_limit_max_trades)
    if wakeup_cycle_trade_limit_enabled
    else 0,
    dtype=np.int64,
)
```

`_allocate_apply_arrays` получает explicit values:

```python
wakeup_cycle_trade_limit_enabled: bool
wakeup_cycle_trade_limit_max_trades: int
```

Не вычислять второй predicate внутри diagnostics.

### 10.2 Writes

Запись счетчика:

```python
if diag_enabled and mode_d_enabled:
    if state in (
        ZigZagFSMState.ST_ACTIVE_FREEZE,
        ZigZagFSMState.ST_STOPPING,
    ):
        wakeup_cycle_trade_count_arr[t] = cycle_trade_count
    else:
        wakeup_cycle_trade_count_arr[t] = -1
```

При срабатывании limit:

```python
if diag_enabled:
    wakeup_exit_cycle_trade_limit_triggered_arr[t] = np.int8(1)
```

Запись reason/action:

```text
wakeup_exit_reason[t] = "cycle_trade_limit"
wakeup_position_action[t] = "exit_cycle_trade_limit"
```

### 10.3 Finalization

В `_finalize_apply_result` добавить поля только в существующий Mode-D-gated
блок. Non-Mode-D diagnostics не должны получить эти keys.

## 11. Lite Mode

Lite behavior должен быть идентичен diagnostic behavior по positions.

Инварианты:

```text
collect_filter_diagnostics=False does not call _allocate_apply_arrays
result.filter_diagnostics is None
positions bit-for-bit equal diagnostic run on same firing limit scenario
```

Runtime decisions не читают diagnostic arrays. Из diagnostic arrays нельзя
выводить:

```text
cycle_trade_count
cycle limit trigger decision
reason/action scalar strings
state transitions
```

## 12. Trade-Level Mapping

Расширить `filter_trade_diagnostics.py`, но не менять существующую
`block_new_entries` close-reason модель.

`_wakeup_exit_reason_at`:

```python
"cycle_trade_limit" -> "wakeup_exit_cycle_trade_limit"
```

`_wakeup_position_action_at`:

```python
"exit_cycle_trade_limit" -> "wakeup_exit_cycle_trade_limit"
```

Trade-level expected fields for `action.mode=close_position`:

```text
wakeup_cycle_exit_reason = cycle_trade_limit
wakeup_position_action = exit_cycle_trade_limit
exit_reason = wakeup_exit_cycle_trade_limit
```

Обязательный edge case: `action.mode=close_position` должен мапиться через
`exit_signal_idx` на trigger bar, а не падать в fallback `st_flip`.

Для `action.mode=block_new_entries` trade-level
`exit_reason=wakeup_exit_cycle_trade_limit` не является acceptance target в этой
реализации. Причина: limit срабатывает на баре `t` и переводит цикл в
`ST_STOPPING`, но позиция закрывается позже на opposite ST flip; существующий
`attach_trade_filter_diagnostics` читает reason на `exit_signal_idx`, то есть
на баре фактического закрытия, где `wakeup_exit_reason` уже `"none"`. Поэтому
trade-level close reason для `block_new_entries` остается совместимым с текущим
поведением `ttl`/`no_fresh_candidate`, обычно `filter_stopping_opposite_flip`.

Per-bar observability для `block_new_entries` обеспечивается полями:

```text
wakeup_exit_reason[t] = cycle_trade_limit
wakeup_position_action[t] = exit_cycle_trade_limit
wakeup_exit_cycle_trade_limit_triggered[t] = 1
```

Если в будущем потребуется trade-level attribution именно для
`block_new_entries`, это отдельная фича: нужно протаскивать wakeup-cycle exit
cause через `ST_STOPPING` до бара фактического закрытия. В текущий scope это не
входит.

## 13. Excel, Export, Summary

### 13.1 Per-bar display names

Добавить в `FILTER_DIAGNOSTICS_100_DISPLAY_NAMES`:

```python
"wakeup_cycle_trade_count": "Wakeup Cycle Trade Count"
"wakeup_exit_cycle_trade_limit_triggered": (
    "Wakeup Exit Cycle Trade Limit Triggered"
)
"wakeup_cycle_trade_limit_config": "Wakeup Cycle Trade Limit Config"
```

Обновить frozen header snapshot в `test_pr5_schema_contract.py`.

### 13.2 Retained export

Retained per-bar export должен pass through новые fields так же, как текущие
wakeup fields. Добавить explicit regression в `test_xlsx_export.py`.

### 13.3 Cycle sheet

Cycle sheet уже считает `Сделок в цикле` из trades по `entry_index`. Для новой
фичи проверить:

```text
max(wakeup_cycle_trade_count over active/closing segment)
==
number of trades whose entry_index belongs to that segment
```

Не использовать OFF sentinel `-1` для проверки segment max.

Для `close_position` trigger bar может быть `OFF`, поэтому segment заканчивается
на предыдущем active bar; это ожидаемо.

### 13.4 Summary

Не добавлять `wakeup_exit_cycle_trade_limit_count` и не добавлять новую строку
в `filters_summary` в рамках этой реализации.

При этом summary builders не должны падать из-за новых reason/action values.
Добавить тест:

```text
cycle_trade_limit reason/action present in diagnostics
_build_filter_diagnostics_summary succeeds
filters_summary succeeds
no wakeup_exit_cycle_trade_limit_count key is emitted
```

Если где-то есть hard whitelist, расширить минимально только для passthrough.

## 14. Work Packages

### WP1 - Config Schema And Validation

Files:

```text
donor/supertrend_optimizer/core/trade_filter_config.py
wf_grid/config/loader.py
wf_grid/tests/test_wp2_config_trade_filter.py
```

Tasks:

```text
add TradeFilterWakeupMaxTradesPerCycleConfig
extend TradeFilterWakeupExitConfig
materialize raw max_trades_per_cycle block
extend shared allowed-key map
extend wf_grid allowed-key mirror
validate strict bool enabled
validate max_trades int >= 1 when enabled
include limit in "at least one enabled exit condition"
export dataclass in __all__
write accepted config tests through donor validator directly
prove wf_grid rejects wakeup_regime as unsupported, not unknown key
```

### WP2 - Runtime Predicate And Disabled Baseline

Files:

```text
donor/supertrend_optimizer/core/zigzag_st_filter.py
wf_grid/tests/test_wakeup_mode_d_entry.py
```

Tasks:

```text
extend _resolve_mode_d_wakeup_exit
derive wakeup_cycle_trade_limit_enabled and max_trades once
add max_trades parameters to test helper _cfg
prove absent block and enabled=false preserve positions
prove wakeup_regime.enabled=false + limit.enabled=true is runtime no-op
```

### WP3 - Runtime State And OFF Reset

Tasks:

```text
add cycle_trade_count scalar
set count=1 on wakeup start
reset count only at actual OFF sites
preserve count in ST_STOPPING
keep _wakeup_runtime_off() call on merely leaving ACTIVE separate from OFF reset
do not attach count reset to _wakeup_runtime_off() when merely leaving ACTIVE
add ST_STOPPING diagnostic preservation test
add daily/time reset count-clear test
add code guard/comment: Mode D does not use ST_COUNTING_ZZ_LEGS, so Exit-B
  immediate-off is unreachable for wakeup-cycle count reset
```

### WP4 - Exit C Trigger

Tasks:

```text
capture Mode-D ttl/no_fresh positions golden before refactor for matrix:
  action.mode {block_new_entries, close_position}
  lock_cycle_direction {false, true}
refactor common Exit C transition body
add _wakeup_exit_action_for_reason
add cycle_trade_limit after ttl and no_fresh_candidate priority
support block_new_entries and close_position
ensure internal ST flip and freeze release do not run after trigger
cover max_trades=1 and max_trades=3
cover priority ttl/no_fresh over cycle limit
```

### WP5 - Counting Opens

Tasks:

```text
increment once from wakeup_position_action_this_bar
count restore_allowed_position_on_st_flip
count reverse_on_st_flip
count position-freeze release when release writes those actions
do not count flat_on_disallowed_st_flip
do not count position_freeze_ignored_opposite_st_flip
cover lock_cycle_direction=false/true
```

### WP6 - Diagnostics And Lite Parity

Tasks:

```text
add three diagnostic arrays following existing unconditional wakeup-array pattern
write count with -1 OFF sentinel
write triggered flag on limit trigger bar
echo max_trades config
prove non-Mode-D diagnostics do not include new keys
spy _allocate_apply_arrays is not called in lite mode
compare diagnostic vs lite positions on firing scenarios
```

### WP7 - Trade Mapping, Excel, Summary

Files:

```text
donor/supertrend_optimizer/core/filter_trade_diagnostics.py
donor/supertrend_optimizer/io/excel_tester.py
donor/supertrend_optimizer/testing/runner.py
wf_grid/tests/test_pr5_schema_contract.py
wf_grid/tests/test_xlsx_export.py
donor TESTER/tests/test_xlsx_cycle_sheet.py
```

Tasks:

```text
map cycle_trade_limit reason/action to wakeup_exit_cycle_trade_limit for close_position
assert block_new_entries keeps current trade-level stopping close reason
cover close_position trade-level exit_reason timing
add display names
update header snapshot
add retained export passthrough test
verify cycle sheet count agreement
verify summary builders do not emit a new aggregate counter
```

## 15. Required Tests

### Config

```text
T1 absent block -> enabled=false and runtime no-op
T2 donor validator accepts enabled=true, max_trades=5
T3 enabled=true without max_trades rejected
T4 max_trades in {0, 2.5, "5", None, True, False} rejected when enabled
T5 enabled in {"true", 1, None} rejected
T6 unknown sibling under max_trades_per_cycle rejected
T7 donor validator accepts only enabled limit as the enabled exit condition
T8 donor validator accepts enabled=true with wakeup_regime.enabled=false as
   runtime no-op
T9 wf_grid rejects config as wakeup_regime unsupported, not unknown key
```

### Runtime

```text
T10 max_trades=3 block_new_entries: third opening counted, next active bar
    fires cycle_trade_limit, no fourth opening
T11 max_trades=1 fires on next active cycle bar after start
T12 close_position sets positions[t+1]=0 and trigger flag=1
T13 block_new_entries from flat checks reason/action/positions, not brittle state
T14 ttl and no_fresh_candidate beat cycle_trade_limit on same bar
T15 after actual OFF, new entry gate starts new count at 1
T16 daily/time reset clears runtime count and closes cycle
T17 lock_cycle_direction=true still counts restores/reverses correctly
T18 position_freeze ignored flip does not count
T19 position_freeze release restore/reverse counts
T20 enabled=false positions match current baseline behavior
T21 wakeup_regime.enabled=false + limit.enabled=true positions unchanged
T22 ST_STOPPING preserves diagnostic count until actual OFF
T23 existing Mode-D ttl/no_fresh positions remain unchanged for matrix:
    action.mode {block_new_entries, close_position}
    lock_cycle_direction {false, true}
T24 code guard/comment documents that Mode D does not use ST_COUNTING_ZZ_LEGS
    and Exit-B immediate-off is unreachable for wakeup-cycle count reset
```

### Diagnostics And Observability

```text
T25 Mode D diagnostics include new fields with dtypes int64, int8, int64
T26 non-Mode-D finalized diagnostics do not include new fields
T27 wakeup_cycle_trade_count is -1 in OFF
T28 on the bar of the Nth opening, wakeup_cycle_trade_count shows N
T29 active/closing max wakeup_cycle_trade_count matches cycle-sheet trade count
T30 retained per-bar export passes through the fields
T31 trade-level block_new_entries limit exit keeps current stopping close reason
T32 trade-level close_position limit exit maps to wakeup_exit_cycle_trade_limit
T33 summary builders succeed and emit no wakeup_exit_cycle_trade_limit_count
```

### Lite

```text
T34 diagnostic and lite positions are identical on firing limit scenario
T35 collect_filter_diagnostics=False returns filter_diagnostics=None
T36 collect_filter_diagnostics=False does not call _allocate_apply_arrays
T37 lite matrix covers:
    max_trades {1, 3, 5}
    action.mode {block_new_entries, close_position}
    lock_cycle_direction {false, true}
```

## 16. Delivery Scope

Это не маленькая single-patch фича: изменение затрагивает config schema,
горячий FSM loop, diagnostics, trade-level attribution и Excel/export
contracts. Безопасная реализация должна идти в 2-3 reviewable changes:

```text
PR 1: config schema, validation, wf_grid mirror, donor/wf_grid config tests.
      Estimated effort: 0.5-1 engineering day.
PR 2: runtime predicate, cycle_trade_count, Exit C trigger, counting,
      disabled baseline, lite parity, Mode-D golden positions.
      Estimated effort: 2-3 engineering days.
PR 3: diagnostics arrays, trade-level close_position mapping, Excel/export,
      cycle sheet, summary no-new-counter checks.
      Estimated effort: 1-2 engineering days.
```

Total planning estimate: 3.5-6 engineering days, excluding time spent on
unexpected regressions in the broader non-slow suite or review turnaround.

Если реализация идет одним PR, порядок из раздела 18 остается обязательным, а
проверки PR 1/2/3 должны быть зелены до перехода к следующему блоку.

## 17. Verification Commands

Focused:

```powershell
python -m pytest wf_grid/tests/test_wp2_config_trade_filter.py -q
python -m pytest wf_grid/tests/test_wakeup_mode_d_entry.py -q
python -m pytest wf_grid/tests/test_xlsx_export.py -q
python -m pytest wf_grid/tests/test_pr5_schema_contract.py -q
python -m pytest "donor TESTER/tests/test_xlsx_cycle_sheet.py" -q
```

Nearby regression:

```powershell
python -m pytest wf_grid/tests/test_wp3_zigzag_global_stats.py wf_grid/tests/test_wp4_zigzag_per_bar.py wf_grid/tests/test_wp5_zigzag_fsm.py -q
python -m pytest wf_grid/tests/test_wp6_st_flip_event_ordering.py wf_grid/tests/test_wp7_backtest_integration.py -q
```

Broader non-slow suite if time allows:

```powershell
python -m pytest wf_grid/tests -m "not slow" -q
```

## 18. Implementation Order

```text
1. Config dataclass, parser, shared allowed keys, validation, __all__.
2. wf_grid allowed-key mirror and unsupported rejection test.
3. Test helper _cfg extension for max_trades_per_cycle.
4. _resolve_mode_d_wakeup_exit expansion to include max_trades_per_cycle.
5. Runtime predicate wakeup_cycle_trade_limit_enabled/max_trades.
6. Disabled/no-op baseline tests.
7. Capture existing Mode-D ttl/no_fresh positions golden before Exit C refactor.
8. cycle_trade_count scalar and actual-OFF reset handling.
9. Start-count = 1 on wakeup start.
10. Exit C common transition refactor.
11. cycle_trade_limit trigger with priority after ttl/no_fresh.
12. Post-action counting rule.
13. Unconditional diagnostic arrays and Mode-D-only finalization keys.
14. Lite parity tests.
15. Trade-level mapping for close_position and current block_new_entries behavior.
16. Excel display names, schema snapshot, retained export test.
17. Cycle-sheet and summary no-new-counter tests.
18. Focused verification, then nearby regression.
```

## 19. Main Risks And Guardrails

```text
Risk: cycle_trade_count resets when state leaves ST_ACTIVE_FREEZE.
Guardrail: count lives through ST_STOPPING; reset only on actual OFF.

Risk: lite mode diverges because logic reads diagnostic arrays.
Guardrail: all trigger decisions use loop-local scalars.

Risk: close_position loses trade-level reason due to exit_signal_idx timing.
Guardrail: explicit close_position trade mapping regression.

Risk: block_new_entries trade-level mapping is over-promised.
Guardrail: keep current stopping close reason at trade level; use per-bar
cycle_trade_limit fields for attribution.

Risk: non-Mode-D diagnostics get new keys.
Guardrail: arrays may be allocated internally, but finalized non-D diagnostics
must not expose the new fields.

Risk: legacy ttl/no_fresh behavior changes during Exit C refactor.
Guardrail: capture positions golden before refactor for action.mode
{block_new_entries, close_position} x lock_cycle_direction {false, true};
priority tests and existing Mode D tests must remain green.

Risk: counter diagnostics are written before post-action increment.
Guardrail: bar-exact test asserts the Nth opening bar reports count N.

Risk: summary/export snapshots break unexpectedly.
Guardrail: update per-bar display snapshot only; assert no new summary counter.
```

## 20. Acceptance Checklist

Implementation is complete when:

```text
config validation matches this contract
wf_grid still rejects wakeup_regime as unsupported
absent/disabled limit preserves positions
wakeup_regime.enabled=false + limit.enabled=true is runtime no-op
cycle_trade_count is runtime-local and lite-safe
count starts at 1 on wakeup start
restore/reverse openings increment exactly once
flat/ignored freeze flips do not increment
cycle_trade_limit fires before N+1 opening
ttl/no_fresh priority beats cycle_trade_limit
block_new_entries preserves count through ST_STOPPING
close_position closes via positions[t+1]=0
actual OFF clears count
Nth opening bar diagnostics show wakeup_cycle_trade_count == N
Mode D diagnostics include exactly the three new fields
non-Mode-D diagnostics do not include them
close_position trade-level exit_reason maps to wakeup_exit_cycle_trade_limit
block_new_entries trade-level exit_reason keeps current stopping close behavior
cycle-sheet trade count agrees with active/closing max count
summary emits no wakeup_exit_cycle_trade_limit_count
lite and diagnostic positions are identical
focused and nearby regression tests pass
```
