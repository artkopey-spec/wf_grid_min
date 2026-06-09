# Implementation Plan: Position Freeze для wakeup_regime (v1)

## 1. Цель и контекст

Снизить убыток от ранних opposite ST flip, которые выбивают позицию через 1-3 бара, не дав ей дожить до прибыльной зоны 6-20 баров.

Доказательная база по отчетам:

```text
revers   : wakeup_reverse_on_st_flip       ~ -92.97% (PF 0.26), Bars<=3 = -75.22%
long     : wakeup_flat_on_disallowed_st_flip = -11.07%, Bars<=3 глубоко минус
both     : reverse_on_st_flip = 0, flat_on_disallowed_st_flip = 1299;
           SHORT flat_on_disallowed = -21.75% (322 сделки Bars<=3)
signal-study: ~30% ранних флипов откатываются за 3 бара, ~72% за 10 баров.
```

Вывод: корень один - краткоживущий opposite ST flip внутри уже открытой позиции. `position_freeze` означает: не действовать по флипу, пока ST не подтвердит разворот в течение `min_hold_bars` баров.

Реалистичное ожидание: апсайд ограничен, особенно при `min_hold_bars=3`. Это улучшение профиля быстрых выбиваний, а не полное исправление edge.

## 2. Терминология и ловушка имен

В коде уже есть FSM-состояние `ZigZagFSMState.ST_ACTIVE_FREEZE` (значение `2`, `donor/supertrend_optimizer/core/zigzag_st_filter.py`) - это активный wakeup-cycle, а не данная фича.

Соглашение: фича называется `position_freeze`; runtime-переменные и диагностические поля получают префикс `pos_freeze_` или `position_freeze_`. Не переиспользовать слово `FREEZE` как новое FSM-состояние.

Различать:

```text
held_pos                : FSM-владелая позиция (-1/0/+1)
wakeup_active_direction : направление wakeup-цикла
cycle_direction         : зафиксированное направление при lock_cycle_direction
trend[t]                : текущий уровень ST (+1/-1)
flip_dir                : событие флипа на баре t (0, если флипа нет)
```

## 3. Конфиг

Фича включается только через явный config-флаг:

```yaml
trade_filter:
  wakeup_regime:
    position_freeze:
      enabled: true
      min_hold_bars: 3
      apply_to: internal_opposite_st_flip
      release_action: apply_if_still_opposite
```

Дефолт обязателен: отсутствие блока или `enabled: false` сохраняет текущее поведение.

Scope v1:

```text
pipeline     : donor/tester only
wf_grid      : продолжает отвергать mode D / exit C / wakeup_regime, включая position_freeze
zigzag.mode  : только D
wakeup       : только при wakeup_regime.enabled=true
trade_mode   : long, short, both, revers
```

Для режимов `A/B/C/A+B/C+B` флаг не должен иметь runtime-эффекта. Если `position_freeze.enabled=true` используется вне `mode D + wakeup_regime.enabled=true` в tester-конфиге, это ConfigError.

Изменения:

```text
trade_filter_config.py
  + dataclass TradeFilterWakeupPositionFreezeConfig
  + поле position_freeze в TradeFilterWakeupRegimeConfig
  + парсинг wakeup_raw.get("position_freeze")
  + whitelist ключей trade_filter.wakeup_regime.position_freeze
  + валидация в _validate_wakeup_regime_block
```

Дефолты dataclass:

```python
enabled: object = False
min_hold_bars: object = None
apply_to: object = None
release_action: object = None
```

Валидация при `enabled=true`:

```text
min_hold_bars  : int >= 1
apply_to       : "internal_opposite_st_flip"
release_action : "apply_if_still_opposite"
```

`position_freeze.enabled=true` требует `wakeup_regime.enabled=true` и `zigzag.mode=D`. При `wakeup_regime.enabled=false` блок допустим только как no-op при `enabled:false` после strict validation. `enabled:true` при выключенном wakeup - ConfigError.

## 4. Состояние в apply()-цикле

Рядом с runtime-состоянием `held_pos`:

```text
pos_freeze_until   : int = -1     # последний бар, на котором окно еще активно
pos_freeze_dir     : int = 0      # snapshot направления позиции
pos_freeze_pending : bool = False # был проигнорирован opposite flip и ждем release
```

Диагностические массивы рядом с `wakeup_position_action_arr`:

```text
pos_freeze_active_arr        int8, default 0
pos_freeze_bars_left_arr     int64, default 0
pos_freeze_ignored_flip_arr  int8, default 0
pos_freeze_release_action_arr object, default "none"
```

Если фича выключена, эти массивы можно экспортировать с дефолтами или не экспортировать, но schema contract должен быть явным и покрыт тестом.

## 5. Инварианты

```text
I1. enabled=false -> pos_freeze_* не влияет на held_pos.
I2. pos_freeze_until>=0 допустим только когда held_pos != 0.
I3. При held_pos -> 0 окно очищается: pos_freeze_until=-1, pos_freeze_dir=0,
    pos_freeze_pending=False.
I4. daily_reset/time_filter_reset/FSM_OFF очищают окно.
I5. TTL и no_fresh_candidate не блокируются заморозкой.
I6. restore_allowed_position_on_st_flip не сбрасывает окно как новый вход/реверс.
I7. Заморозка действует только на внутренний opposite flip активного wakeup-cycle,
    не на вход, не на exit-C и не на внешние reset/stop-сценарии.
```

## 6. Порядок обработки на баре t

Текущий порядок внутри `if mode_d_enabled`:

```text
A. reset-обработка
B. вычисление wakeup_active_direction / fresh
C. exit-C: ttl / no_fresh_candidate             # вне freeze
D. wakeup_internal_st_flip_this_bar + apply flip # главная врезка
E. запись wakeup_position_action
F. FSM transitions, WAIT->FREEZE, held_pos=flip_dir
```

Заморозка вклинивается в шаг D и добавляет release-проверку D2. Установка нового окна делается централизованно после FSM-переходов, перед итоговой записью позиции на следующий бар.

## 7. Перехват opposite flip во время окна

Определение:

```text
opposite_to_position = flip_dir != 0 and held_pos != 0 and flip_dir == -held_pos
freeze_active = position_freeze.enabled and held_pos != 0 and t <= pos_freeze_until
```

Логика:

```text
IF freeze_active AND opposite_to_position:
    held_pos unchanged
    wakeup_active_direction unchanged
    wakeup_position_action_this_bar = "position_freeze_ignored_opposite_st_flip"
    pos_freeze_pending = True
    pos_freeze_ignored_flip_arr[t] = 1
ELSE:
    run existing _apply_mode_d_internal_st_flip(...)
```

Перехват ставится до выбора `effective_trade_mode`: решение игнорировать флип зависит от направления флипа относительно `held_pos`, а не от `trade_mode`.

## 8. Установка окна

Не рассыпать установку по точкам входа. В конце бара после FSM-переходов:

```text
opened = held_pos_at_bar_start == 0 and held_pos != 0
reversed_ = (
    held_pos_at_bar_start != 0
    and held_pos != 0
    and sign(held_pos) != sign(held_pos_at_bar_start)
)

IF position_freeze.enabled AND (opened OR reversed_):
    pos_freeze_dir = held_pos
    pos_freeze_until = t + min_hold_bars
    pos_freeze_pending = False
```

Семантика `min_hold_bars`:

```text
entry/reverse на t0
freeze active на барах [t0+1 .. t0+min_hold_bars] включительно
flip внутри окна, если t <= pos_freeze_until

Пример: t0=1, min_hold_bars=3 -> pos_freeze_until=4.
Флипы на 2,3,4 игнорируются; на 5 обрабатываются.
```

На самом баре входа/реверса внутренний ST flip не должен попадать в окно из-за guard-ов `state_at_bar_start`.

## 9. Release-логика

После игнора флипа нового `flip_dir != 0` может не быть: ST просто остается в противоположном уровне. Поэтому release использует уровень `trend[t]`, а не сохраненное событие флипа.

Release-проверка выполняется каждый бар активного wakeup-cycle:

```text
release_due = (
    position_freeze.enabled
    and held_pos != 0
    and pos_freeze_pending
    and t > pos_freeze_until
    and not is_reset
    and not wakeup_exit_c_triggered_this_bar
)

IF release_due:
    st_now = trend[t]  # +1/-1

    IF st_now == held_pos:
        pos_freeze_pending = False
        pos_freeze_release_action_arr[t] = "noop_st_realigned"
    ELSE:
        apply _apply_mode_d_internal_st_flip(
            held_pos=held_pos,
            wakeup_active_direction=...,
            flip_dir=st_now,
            trade_mode=effective_trade_mode,
        )
        pos_freeze_pending = False
        pos_freeze_release_action_arr[t] = "applied_" + wakeup_position_action_this_bar
```

Детали:

```text
R1. st_now - уровень ST, не событие.
R2. При lock_cycle_direction effective_trade_mode берется из cycle_direction.
    cycle_direction=+1 -> effective_trade_mode="long";
    cycle_direction=-1 -> effective_trade_mode="short".
    Запрещено использовать сырой trade_mode для release под lock_cycle_direction,
    иначе both/revers может ошибочно сделать reverse против lock.
R3. Если release сделал reverse без lock_cycle_direction, §8 поставит новое окно
    для новой позиции.
R4. Если до release ST вернулся к позиции, pending можно снять сразу или на D2.
R5. exit-C имеет приоритет над release.
R6. Если release применил flat и held_pos стал 0, окно очищается по I3.
```

Поведение по `trade_mode`:

```text
long:
    LONG + ST SHORT внутри окна -> ignore.
    release, если ST все еще SHORT -> flat_on_disallowed_st_flip.

short:
    SHORT + ST LONG внутри окна -> ignore.
    release, если ST все еще LONG -> flat_on_disallowed_st_flip.

both/revers без lock_cycle_direction:
    opposite ST внутри окна -> ignore.
    release, если ST все еще opposite -> reverse_on_st_flip.

both/revers с lock_cycle_direction=true:
    raw trade_mode не используется для release.
    cycle_direction=+1 ведет себя как long.
    cycle_direction=-1 ведет себя как short.
    Итог release при подтвержденном opposite ST -> flat_on_disallowed_st_flip,
    а не reverse_on_st_flip.
```

## 10. Взаимодействия и исключения

```text
daily_reset / time_filter_reset:
    очистить pos_freeze_* до дальнейшей логики.

exit-C ttl / no_fresh:
    выполняется до D/D2. Если сработал exit-C, release не выполняется.

close_position:
    held_pos=0 -> очистить окно.

block_new_entries:
    позиция может оставаться открытой; freeze не должен ломать текущую exit-C
    семантику.

lock_cycle_direction:
    перехват не зависит от lock, release использует effective_trade_mode,
    согласованный с cycle_direction.
    Под lock_cycle_direction=true freeze совместим со всеми raw trade_mode,
    но release намеренно сводится к long/short по cycle_direction.

FSM_OFF / конец данных:
    окно очищается, если позиция сброшена или цикл завершен.
```

## 11. Диагностика и экспорт

В `zigzag_st_filter.py` экспортировать новые массивы в diagnostics out-dict:

```text
position_freeze_active
position_freeze_bars_left
position_freeze_ignored_opposite_st_flip
position_freeze_release_action
```

В `filter_trade_diagnostics.py`:

```text
"position_freeze_ignored_opposite_st_flip"
    -> "wakeup_position_freeze_ignored_opposite_st_flip"
```

В `FilterDiagnostics_100`:

```text
Wakeup Position Freeze Active
Wakeup Position Freeze Bars Left
Wakeup Position Freeze Ignored Opposite ST Flip
Wakeup Position Freeze Release Action
```

В `filters_summary`:

```text
wakeup_position_freeze_ignored_opposite_st_flip_count
wakeup_position_freeze_release_flat_count
wakeup_position_freeze_release_reverse_count
wakeup_position_freeze_release_noop_count
```

Счетчики release должны считаться по `position_freeze_release_action`, а не только по `wakeup_position_action`, чтобы отличать обычный ST flip от release после freeze.

## 12. Тесты

По образцу `wf_grid/tests/test_wakeup_mode_d_entry.py` и `wf_grid/tests/test_wp2_config_trade_filter.py`:

```text
T1 config: парсинг + дефолты, нет блока -> enabled=False.
T2 config: валидация min_hold_bars>=1, apply_to, release_action.
T3 регрессия: enabled=false -> результат идентичен baseline.
T4 ignore: вход -> opposite flip внутри окна -> held_pos неизменен,
   action = position_freeze_ignored_opposite_st_flip.
T5 release noop: после окна ST вернулся в сторону позиции -> noop_st_realigned.
T6 release apply long/short: после окна ST против -> flat_on_disallowed_st_flip.
T7 release apply both/revers: после окна ST против -> reverse_on_st_flip,
   новое окно для новой позиции.
T8 exit-C priority: TTL на баре истечения окна -> exit_ttl, release не стреляет.
T9 reset: daily_reset во время окна -> окно очищено, позиция сброшена.
T10 lock_cycle_direction: release использует cycle_direction.
T10b lock_cycle_direction + raw trade_mode=both/revers: после release получаем
   flat_on_disallowed_st_flip, не reverse_on_st_flip.
T11 boundary: min_hold_bars=1, флип ровно на pos_freeze_until игнорируется.
T12 flat cleanup: release/apply переводит held_pos в 0 -> pos_freeze_* очищены.
T13 diagnostics schema: новые поля имеют стабильные dtype/defaults.
T14 pipeline/scope: wf_grid продолжает отвергать wakeup_regime/position_freeze;
   tester принимает только mode D + wakeup_regime.
```

## 13. Сетка прогонов и метрики

```yaml
min_hold_bars: [0_baseline, 2, 3, 4, 5, 6]
trade_mode: [long, short, both]
```

`revers` прогонять отдельно, если конфиг снова возвращает `reverse_on_st_flip`.
Для `lock_cycle_direction=true` обязательно прогнать raw `both/revers`, чтобы
подтвердить отсутствие reverse и корректный release в flat.

Метрики:

```text
Sum PnL %
Profit Factor
Max Drawdown
Avg Trade %
Bars Held <= 3: count, Sum PnL %, Win Rate
flat_on_disallowed_st_flip: count, Sum PnL %
reverse_on_st_flip: count, Sum PnL %
wakeup_exit_ttl: count, Sum PnL %
position_freeze_ignored / release_flat / release_reverse / release_noop counts
```

Критерий успеха:

```text
Bars Held <= 3 count и убыток снизились
PnL по flat_on_disallowed улучшился
Max DD не вырос критически
TTL-бакет не деградировал
EV(H) имеет устойчивую форму, а не одну удачную точку
```

## 14. Открытые вопросы / решения по умолчанию

```text
Q1. Считать окно по барам цикла или барам позиции?
    Решение: по индексу бара входа/реверса.

Q2. apply_if_still_opposite или hard apply после окна?
    Решение v1: только apply_if_still_opposite.

Q3. Блокировать ли TTL/no_fresh freeze-ом?
    Нет. Exit-C остается приоритетным.

Q4. Ставить ли новое окно после reverse?
    Да, свежий reverse тоже защищается, но только когда reverse реально
    разрешен. Под lock_cycle_direction release не должен делать reverse.

Q5. Снимать ли pending при restore-флипе до истечения окна?
    Можно снять сразу для чистой диагностики, но release-noop на следующем баре
    тоже корректен. Предпочтение v1: снять сразу и записать release/noop только
    когда окно фактически завершилось.
```

## 15. Замечания к реализации

1. `wakeup_position_action_this_bar` уже используется как причина закрытия сделок. Новое значение `position_freeze_ignored_opposite_st_flip` не должно закрывать сделку и не должно попадать в trade close reason как exit.

2. Release-счетчики лучше строить из отдельного `position_freeze_release_action`, иначе будет невозможно отделить обычный `flat_on_disallowed_st_flip` от `applied_flat_on_disallowed_st_flip` после freeze.

3. После любого перехода `held_pos` в `0` нужно централизованно очищать `pos_freeze_*`, чтобы не получить stale pending в следующем wakeup-cycle.

4. Перед широкими прогонами сделать маленький unit-test на exact boundary: `t0`, `t0+H`, `t0+H+1`. Это самый вероятный источник off-by-one.

5. `lock_cycle_direction` - отдельный guardrail. В этом режиме release обязан
   работать через `cycle_direction -> effective_trade_mode`, поэтому raw
   `trade_mode=both/revers` не должен приводить к `reverse_on_st_flip`.
