# ТЗ: Tester-Only лимит числа сделок в wakeup-цикле (Mode D)

## 1. Цель

Добавить в `tester` opt-in ограничение на число фактических входов в позицию внутри одного активного `wakeup_regime`-цикла (`zigzag.mode: D`). По достижении лимита текущий цикл завершается через стандартный механизм Exit C; чтобы возобновить торговлю, стратегия обязана заново пройти полный entry-gate, то есть открыть новый цикл.

Гипотеза: циклы с 3–5 сделками прибыльны, циклы с 7+ сделками стабильно ухудшают результат. Длинный TTL держит один цикл активным надолго, и дешёвые перезаходы по внутренним ST-flip плодят избыточные сделки. Лимит обрезает этот хвост.

## 2. Область действия

Флаг влияет на runtime только когда одновременно:

```text
trade_filter.enabled = true
trade_filter.zigzag.mode = D
trade_filter.wakeup_regime.enabled = true
trade_filter.wakeup_regime.exit.max_trades_per_cycle.enabled = true
```

Ограничения:

```text
- wf_grid НЕ поддерживает фичу: весь wakeup_regime в wf_grid отвергается как
  unsupported; лимит не делает wakeup_regime допустимым в wf_grid.
- режимы A / B / C / A+B / C+B не затрагиваются.
- tester-only обеспечивается на уровне validation layer; caller_pipeline в runtime
  не протаскивается и не используется для ветвления в apply().
- при enabled=false или отсутствии блока поведение бит-в-бит идентично текущему.
- фича работает совместно с lock_cycle_direction (false/true) и position_freeze.
```

## 3. Конфиг

Лимит — третье exit-условие рядом с `ttl` и `no_fresh_candidate`; он завершает цикл и переиспользует общий `exit.action.mode`.

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
        mode: block_new_entries   # или close_position
```

Пути полей:

```text
trade_filter.wakeup_regime.exit.max_trades_per_cycle.enabled
trade_filter.wakeup_regime.exit.max_trades_per_cycle.max_trades
```

Дефолты и валидация:

```text
enabled       -> default false; строго bool; "true"/1/null отвергаются.
max_trades    -> default null; при enabled=true обязателен; целое >= 1;
                 нецелое / < 1 / null при enabled=true -> reject.
отсутствие блока max_trades_per_cycle -> эквивалентно enabled=false.
unknown sibling-ключи внутри max_trades_per_cycle -> reject.
enabled=true при wakeup_regime.enabled=false -> проходит strict-валидацию типов,
                 но является no-op в runtime (отдельной ошибки не вводить).
```

Семантика `max_trades`:

```text
max_trades = N  -> в одном цикле допускается не более N открытий позиции,
                   включая стартовый вход. (N+1)-е открытие не происходит,
                   потому что по достижении N цикл переводится в exit-режим
                   на следующем активном баре.
max_trades = 1  -> только стартовый вход. На следующем активном баре цикла
                   срабатывает cycle_trade_limit. Поведение детерминировано
                   и НЕ зависит от trade_mode (см. §6).
```

Правило «at least one enabled exit condition»: `max_trades_per_cycle` учитывается как включённое exit-условие наравне с `ttl`/`no_fresh_candidate`. Конфиг только с включённым лимитом обязан проходить валидацию.

## 4. Определения

```text
Цикл (wakeup-cycle):
    непрерывный участок, где FSM не в OFF.
    Старт: state_at_bar_start == OFF -> ST_ACTIVE_FREEZE,
           trigger_source = wakeup_regime.
    Конец: фактический возврат в OFF.

Открытие позиции (то, что считает лимит) — определяется по
wakeup_position_action_this_bar и факту старта:
    стартовый вход цикла                          -> открытие (count := 1)
    restore_allowed_position_on_st_flip           -> открытие (count += 1)
    reverse_on_st_flip                            -> открытие (count += 1)
    (та же метка, выставленная из position_freeze release path, тоже считается)
    flat_on_disallowed_st_flip                    -> НЕ открытие (закрытие)
    position_freeze_ignored_opposite_st_flip      -> НЕ открытие (позиция не менялась)
```

Источник истины для счёта — loop-local скаляр и scalar-метка бара `wakeup_position_action_this_bar`, а не diagnostic-массивы (важно для lite-режима, §8).

## 5. Runtime-состояние

Вводится loop-local скаляр:

```text
cycle_trade_count   # число открытий позиции в текущем wakeup-cycle
```

Инварианты runtime-скаляра:

```text
- init / вне active-or-closing цикла: cycle_trade_count = 0
- старт wakeup-cycle:                  cycle_trade_count = 1
- ST_ACTIVE_FREEZE:                    cycle_trade_count хранит число открытий
- ST_STOPPING после block_new_entries: cycle_trade_count сохраняется как контекст цикла
- фактический OFF:                     cycle_trade_count = 0
```

Diagnostic sentinel отличается от runtime-скаляра:

```text
- runtime cycle_trade_count вне цикла = 0
- diagnostic wakeup_cycle_trade_count в OFF = -1
- diagnostic wakeup_cycle_trade_count в ST_ACTIVE_FREEZE / ST_STOPPING =
  текущее значение runtime cycle_trade_count
```

Сброс счётчика выполняется только в точках фактического перехода в OFF. Переход `block_new_entries -> ST_STOPPING` счётчик НЕ сбрасывает. Для устранения риска «забыли сбросить в одном из переходов» вводится единый helper, сбрасывающий весь per-cycle runtime разом (как минимум `cycle_direction`, `cycle_trade_count` и wakeup-runtime поля), и вызывается во всех OFF-sites:

```text
- init (до петли)
- combined-reset wipe (daily_reset / time_filter_reset) -> OFF
- Exit C close_position -> OFF
- нормализация ST_STOPPING при cur_pos == 0 -> OFF
- ST_STOPPING close on opposite ST flip -> OFF
- любой иной фактический переход wakeup-cycle в OFF
```

Инкременты `cycle_trade_count` выполняются БЕЗУСЛОВНО, независимо от `collect_filter_diagnostics`:

```text
на баре старта цикла:                         cycle_trade_count = 1
на барах, где wakeup_position_action_this_bar
  ∈ {restore_allowed_position_on_st_flip,
     reverse_on_st_flip}:                      cycle_trade_count += 1
```

Эта метка проставляется как в обычном internal-ST-flip path, так и в position_freeze release path, поэтому одно правило покрывает оба случая — без дублирования логики подсчёта.

## 6. Поведение при достижении лимита (как третий Exit C-триггер)

`cycle_trade_limit` проверяется в том же Exit C-блоке, что `ttl` и `no_fresh_candidate` (блок выполняется в начале бара при `state_at_bar_start == ST_ACTIVE_FREEZE`, до internal-ST-flip handling). Приоритет:

```text
ttl  >  no_fresh_candidate  >  cycle_trade_limit
```

Условие срабатывания:

```text
max_trades_per_cycle.enabled AND cycle_trade_count >= max_trades
```

При срабатывании используется существующая Exit C-машинерия:

```text
wakeup_exit_reason_this_bar      = "cycle_trade_limit"
wakeup_position_action_this_bar  = "exit_cycle_trade_limit"
wakeup_exit_cycle_trade_limit_triggered[t] = 1   (diag-only)
далее по exit.action.mode:
    block_new_entries -> state = ST_STOPPING,
                         cycle_trade_count сохраняется до фактического OFF
    close_position     -> state = OFF, held_pos = 0,
                          wakeup_exit_close_triggered[t]=1 (diag-only),
                          сброс per-cycle runtime (§5)
```

Обязательные инварианты:

```text
- block_new_entries:
    cycle_trade_limit fires -> ST_STOPPING
    cycle_trade_count сохраняется в ST_STOPPING до фактического OFF
    wakeup_exit_c_fired = True, поэтому Exit C не срабатывает повторно
    cycle_trade_count обнуляется только в OFF через единый reset helper

- close_position:
    cycle_trade_limit fires -> OFF на decision-баре
    positions[t+1] = 0 по open-to-open контракту
    cycle_trade_count сбрасывается через единый reset helper
```

Почему это снимает прежние противоречия:

```text
- Таймаут срабатывания детерминирован: count достигает max на баре N-го открытия;
  на следующем активном баре (state_at_bar_start == ST_ACTIVE_FREEZE) Exit C-блок
  фиксирует cycle_trade_limit ДО internal-ST-flip handling, поэтому (N+1)-е
  открытие невозможно. Отдельная per-flip suppression не нужна;
  метка blocked_cycle_trade_limit НЕ вводится.

- max_trades=1: после старта (count=1) лимит срабатывает на следующем активном
  баре одинаково для long/short и both/revers. Никакой зависимости от того,
  является ли opposite flip "флетом" или "разворотом", нет.

- position_freeze: лимит срабатывает в Exit C-блоке, а не внутри release path;
  release/restore лишь инкрементит счётчик. Дополнительных release-меток не нужно.
```

## 7. Open-to-open контракт и состояние на баре срабатывания

```text
- cycle_trade_count инкрементируется на decision-баре t, на котором будет записан
  positions[t+1] (контракт open-to-open сохраняется без изменений).
- "немедленное закрытие" при close_position означает: на баре срабатывания t
  positions[t+1] = 0 (исполнение по ближайшему open(t+1)).
- block_new_entries: на баре срабатывания state становится ST_STOPPING;
  если на этом баре открытой позиции уже нет (cur_pos == 0), существующая
  нормализация ST_STOPPING -> OFF переводит state в OFF на том же баре.
  Поэтому в acceptance состояние на trigger-баре описывается как
  "ST_STOPPING, который может нормализоваться в OFF на том же баре при cur_pos==0";
  проверять надо причину/action/positions, а не жёсткий state.
- при block_new_entries, пока цикл находится в ST_STOPPING и ещё не перешёл в OFF,
  diagnostic wakeup_cycle_trade_count продолжает показывать значение счётчика
  на момент срабатывания лимита.
```

## 8. Поддержка lite-режима (lite-parity)

Требование:

```text
При collect_filter_diagnostics=False:
    - diagnostic-массивы не аллоцируются (_allocate_apply_arrays не вызывается);
    - apply() возвращает filter_diagnostics=None;
    - positions идентичны бит-в-бит режиму collect_filter_diagnostics=True.
```

Выполняется БЕЗУСЛОВНО (влияет на positions):

```text
- cycle_trade_count и все инкременты/сбросы;
- проверка cycle_trade_limit в Exit C-блоке и переход по action.mode;
- scalar-метки бара: wakeup_exit_reason_this_bar, wakeup_position_action_this_bar,
  wakeup_exit_c_fired / wakeup_exit_c_triggered_this_bar.
```

Только диагностика (под `collect_filter_diagnostics` / `if diag_enabled:`):

```text
- аллокация и запись новых массивов (§9);
- запись строковых меток в wakeup_exit_reason_arr / wakeup_position_action_arr.
```

Запрет: решение о срабатывании лимита нельзя выводить из значений diagnostic-массивов; только из loop-local скаляров. Иначе в lite-режиме логика отвалится и positions разойдутся.

## 9. Диагностика

Новые per-bar поля добавляются строго внутри Mode-D-gated wakeup-diagnostics блока (для non-Mode-D появляться не должны):

```text
wakeup_cycle_trade_count                 int64; -1 в OFF; текущий счётчик в
                                          ST_ACTIVE_FREEZE/ST_STOPPING
wakeup_exit_cycle_trade_limit_triggered  int8;  1 на баре срабатывания, иначе 0
wakeup_cycle_trade_limit_config          int64; = max_trades при enabled; 0 при disabled
```

Расширение существующих строковых полей:

```text
wakeup_exit_reason     += "cycle_trade_limit"
wakeup_position_action += "exit_cycle_trade_limit"
```

(метка `blocked_cycle_trade_limit` не вводится — см. §6.)

## 10. Файлы для синхронной правки

```text
donor/supertrend_optimizer/core/trade_filter_config.py
    - dataclass TradeFilterWakeupMaxTradesPerCycleConfig(enabled, max_trades)
    - поле TradeFilterWakeupExitConfig.max_trades_per_cycle
    - материализация в build_trade_filter_config_from_raw
    - TRADE_FILTER_ALLOWED_KEYS["trade_filter.wakeup_regime.exit"] += "max_trades_per_cycle"
    - TRADE_FILTER_ALLOWED_KEYS[".exit.max_trades_per_cycle"] = {"enabled","max_trades"}
    - strict-валидация (bool enabled, int>=1 max_trades) в _validate_wakeup_regime_block
    - учёт лимита в правиле "at least one enabled exit condition"
    - экспорт нового dataclass в __all__

wf_grid/config/loader.py
    - _ALLOWED_KEYS["trade_filter.wakeup_regime.exit"] += "max_trades_per_cycle"
    - _ALLOWED_KEYS["trade_filter.wakeup_regime.exit.max_trades_per_cycle"]
          = {"enabled","max_trades"}
    (чтобы wf_grid отвергал конфиг по причине "wakeup_regime unsupported",
     а не по "unknown config key")

donor/supertrend_optimizer/core/zigzag_st_filter.py
    - loop-local cycle_trade_count + единый reset helper во всех OFF-sites
    - третий Exit C-триггер cycle_trade_limit
    - новые массивы в _allocate_apply_arrays (+ echo max_trades в *_config_arr)
    - запись массивов под if diag_enabled:

donor/supertrend_optimizer/core/filter_trade_diagnostics.py
    - _wakeup_exit_reason_at: "cycle_trade_limit" -> "wakeup_exit_cycle_trade_limit"
    - _wakeup_position_action_at: "exit_cycle_trade_limit" -> "wakeup_exit_cycle_trade_limit"

donor/supertrend_optimizer/io/excel_tester.py
    - display-name пары для трёх новых wakeup-полей

тесты-контракты (обновить ожидания):
    - frozen per-bar headers snapshot (schema-contract тест) -> новые display-names
    - test_wakeup_mode_d_entry.py:
        exact keyset (добавить 3 wakeup_-ключа),
        dtype-словарь (добавить 3 поля),
        issubset для wakeup_exit_reason (+ "cycle_trade_limit"),
        issubset для wakeup_position_action (+ "exit_cycle_trade_limit")
```

## 11. Рефакторинг (минимальный)

```text
- Выделить тело "Exit C сработал" (set reason/action/triggered/state по action.mode)
  в общий helper, чтобы три триггера (ttl, no_fresh_candidate, cycle_trade_limit)
  не дублировали код перехода.
- Единый _reset_wakeup_cycle_runtime() (cycle_direction + cycle_trade_count + прочее)
  во всех OFF-sites вместо размазанных ручных сбросов.
- Подсчёт открытий — одним правилом по wakeup_position_action_this_bar, чтобы не
  дублировать детект открытия в обычном и release путях.
```

## 12. Summary / export scope

```text
- Агрегаты в summary (например wakeup_exit_cycle_trade_limit_count) в v1 НЕ
  добавляются. Эффект фичи наблюдаем через:
    cycle-sheet, колонка "Сделок в цикле" (число входов в цикле падает),
    per-bar wakeup_exit_reason / wakeup_exit_cycle_trade_limit_triggered,
    trade-level exit_reason = wakeup_exit_cycle_trade_limit.
- Обязательная проверка: существующие summary/false-start/filters-summary билдеры
  не падают и не теряют строки на новых значениях reason/action (passthrough).
  Если где-то есть жёсткий whitelist причин -> расширить минимально.
```

## 13. Acceptance-тесты

Конфиг-лоадер (tester):

```text
1.  блок отсутствует -> enabled=false, no-op
2.  enabled=true, max_trades=5 -> принят
3.  enabled=true без max_trades -> reject
4.  max_trades в {0, 2.5, "5", null} при enabled=true -> reject
5.  enabled нестрого bool -> reject
6.  unknown sibling-ключ внутри max_trades_per_cycle -> reject
7.  конфиг только с включённым лимитом проходит "at least one enabled exit condition"
8.  enabled=true при wakeup_regime.enabled=false -> принят как no-op
```

wf_grid:

```text
9.  wf_grid с wakeup_regime.exit.max_trades_per_cycle -> reject по причине
    "wakeup_regime unsupported", НЕ по "unknown config key"
```

Runtime:

```text
10. base (trade_mode: long, block_new_entries, max_trades=3):
    стартовый вход + перезаходы; на следующем активном баре после 3-го открытия
    срабатывает cycle_trade_limit; (N+1)-е открытие не происходит;
    wakeup_exit_reason="cycle_trade_limit"; цикл уходит в ST_STOPPING -> OFF
11. max_trades=1: только стартовая сделка; на следующем активном баре цикла
    срабатывает cycle_trade_limit; детерминировано для long и для both/revers
12. close_position: на баре срабатывания positions[t+1]=0, state=OFF,
    wakeup_position_action="exit_cycle_trade_limit",
    wakeup_exit_close_triggered=1
13. block_new_entries из flat: на trigger-баре проверять reason/action/positions;
    state описывать как "ST_STOPPING, может нормализоваться в OFF на том же баре"
14. приоритет: при совпадении на одном баре ttl/no_fresh выигрывают у cycle_trade_limit
15. новый цикл после исчерпания: после OFF и повторного прохождения entry-gate
    стартует новый цикл, cycle_trade_count снова с 1
16. reset внутри цикла: счётчик сброшен, цикл закрыт
17. lock_cycle_direction=true: перезаходы считаются и обрезаются корректно
18. position_freeze: ignored flip не инкрементит; applied restore/reverse инкрементит
19. enabled=false: positions бит-в-бит совпадают со старым прогоном (регрессия)
20. block_new_entries: после срабатывания cycle_trade_limit, пока state==ST_STOPPING,
    wakeup_cycle_trade_count == значение на момент срабатывания; счётчик не сброшен
    и становится diagnostic -1/runtime 0 только при фактическом OFF
```

Наблюдаемость счётчика и согласованность с cycle-sheet:

```text
21. в полном (diag) прогоне: максимум активного/closing wakeup_cycle_trade_count внутри
    bar-сегмента цикла == число сделок с entry_index в этом сегменте
    (== колонка "Сделок в цикле"). Не опираться на reset-sentinel в OFF.
```

Lite-parity:

```text
22. один вход, два вызова apply() (True/False) -> positions идентичны бит-в-бит;
    сценарий обязан реально срабатывать лимитом
23. collect_filter_diagnostics=False -> result.filter_diagnostics is None;
    _allocate_apply_arrays НЕ вызывается (проверить monkeypatch-spy/мок)
24. lite-parity на матрице: max_trades в {1,3,5} x action.mode
    {block_new_entries, close_position} x lock_cycle_direction {false,true}
```

Диагностика и trade-level:

```text
25. Mode D: новые wakeup-поля присутствуют (keyset/dtype обновлены);
    non-Mode-D: не появляются; retained per-bar export проходит passthrough
26. trade-level: при выходе по лимиту wakeup_cycle_exit_reason="cycle_trade_limit",
    exit_reason="wakeup_exit_cycle_trade_limit"
```

## 14. Границы реализации

```text
Разрешено менять:
    core/trade_filter_config.py, core/zigzag_st_filter.py,
    core/filter_trade_diagnostics.py, io/excel_tester.py,
    wf_grid/config/loader.py (только allowed-keys mirror),
    соответствующие tester diagnostics/export mappings и тесты.

Не менять:
    режимы A/B/C/A+B/C+B; расчёт candidate_leg_direction;
    open-to-open контракт; wf_grid pipeline и его reject-политику (кроме allowed-keys);
    поведение при enabled=false; механику ttl/no_fresh/action.mode сверх
    добавления нового триггера.
```

## 15. Нецели

```text
- не делать лимит глобальным счётчиком по всему прогону (только внутри цикла);
- не ограничивать число будущих циклов;
- не вводить caller_pipeline-ветвление в runtime;
- не добавлять поддержку фичи в wf_grid;
- не вводить per-flip suppression и метку blocked_cycle_trade_limit;
- не расширять summary-агрегаты в v1.
```
