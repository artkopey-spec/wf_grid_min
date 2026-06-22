# ТЗ v3: Wakeup Cycle Take Profit Exit (implementable final)

## 0. Архитектурные решения (зафиксированы)

1. **Расчёт PnL — exact-inline** (Вариант 1). Накопитель — runtime-скаляр FSM, без второго прохода, без look-ahead, без рекурсии.
2. **Scope — tester-only (Phase 0).** `cycle_take_profit` — часть `wakeup_regime`/Mode D, которые в WF/Grid запрещены gate'ом `wakeup_regime_unsupported_pipeline`. Снятие gate — отдельное архитектурное решение вне этого ТЗ. Здесь фича включается только для tester pipeline; WF/Grid-правки ограничены parity/dead-code (определение counter в обоих summary-builder'ах), чтобы при будущем снятии gate счётчик уже существовал. WF/Grid-конфиг с `cycle_take_profit` обязан по-прежнему reject'иться.
3. **Источник истины net PnL — единый helper**, переиспользуемый и `trades.extract_trades`, и FSM-накопителем. Рефакторинг `trades.py` обязан быть behavior-preserving: числа и строки `trades_df` не меняются.
4. **Bit-for-bit определён через rounded micro-percent** (см. §3).

## 1. Назначение

Новый Exit C `cycle_take_profit` в `trade_filter.wakeup_regime.exit`. Выключает текущий wakeup-cycle, когда сумма realized net PnL **закрытых** сделок цикла достигает порога. Открытая позиция в расчёте не участвует. Фича — только tester pipeline.

## 2. Конфиг

```yaml
trade_filter:
  wakeup_regime:
    exit:
      cycle_take_profit:
        enabled: true
        pnl_pct: 0.01
      action:
        mode: close_position
```

`pnl_pct` — fraction:

```text
0.01  = +1%
0.005 = +0.5%
0.025 = +2.5%
```

## 3. PnL-семантика и контракт точности

Runtime-накопитель (источник истины поведения), хранится в **integer micro-percent**:

```text
cycle_realized_pnl_micropct : int
```

Это сумма:

```text
round(closed_trade.net_pnl_pct, 6) * 1_000_000
```

Диагностическое зеркало (только при `collect_filter_diagnostics=True`) хранится в fraction-единицах для читабельности:

```text
wakeup_cycle_realized_pnl_pct[t] : float = cycle_realized_pnl_micropct / 1e8
```

Пояснение: `net_pnl_pct` — в процентах; деление на `1e8` даёт fraction (`1.0% -> 0.01`).

Единый helper source-of-truth:

```text
closed_leg_trade_economics(
    entry_idx,
    exit_idx,
    direction,
    positions,
    execution_prices,
    commission_rate,
) -> {
    gross_pnl_pct,
    commission_pct,
    net_pnl_pct,
}
```

Helper возвращает ровно те rounded значения (`6` знаков), которые окажутся в `trades_df` для этой закрытой ноги, включая:

```text
opening-commission-from-flat
closing-side reversal-split (F-16)
```

FSM-накопитель использует из этого helper только `net_pnl_pct`, но `extract_trades` использует весь economics dict. Это нужно, чтобы после рефакторинга не осталось смешанной модели, где часть trade-полей считается старым путём, а часть новым.

Накопление и порог в integer-арифметике:

```text
leg_micropct = int(round(closed_leg_trade_economics(...).net_pnl_pct, 6) * 1_000_000)
cycle_realized_pnl_micropct += leg_micropct

threshold_micropct = int(round(cycle_take_profit.pnl_pct * 1e8))

trigger:
  cycle_realized_pnl_micropct >= threshold_micropct
```

Открытая позиция игнорируется: unrealized PnL не входит в накопитель.

## 4. Рефакторинг `trades.py` (single source of truth)

Выделить из `extract_trades` helper уровня «closed leg trade economics» с явным API (§3). Он локально применяет:

```text
gross_pnl_pct из execution_prices[entry_idx]/execution_prices[exit_idx] (open)
commission_pct = sum(commission_per_bar[entry_idx:exit_idx]) * 100
Adjustment 1: opening-from-flat, если positions[entry_idx - 1] == 0
Adjustment 2: closing-side reversal split F-16, если на exit_idx разворот ± -> ∓
округление gross_pnl_pct, commission_pct, net_pnl_pct до 6 знаков, как сейчас
```

`extract_trades` обязан вызывать этот helper, чтобы не было двух источников истины.

Constraint: рефакторинг behavior-preserving. Regression guard должен быть реализован через golden/reference fixtures с ожидаемыми строками `trades_df` для:

```text
flat-to-flat
reversal
pending/open-at-end
daily/time reset close
```

Старую production-логику не нужно сохранять рядом в коде только ради теста. Если нужна reference-функция, она должна жить только в тестах и быть явно замороженной как legacy oracle.

## 5. Момент срабатывания (адаптация к текущему loop)

Текущий loop: на баре `t` решение принимается и пишется в `filtered_positions[t+1]`; `filtered_positions[t-1]` и `filtered_positions[t]` к началу обработки `t` уже материализованы.

Implementation constraint, порядок на баре `t`:

```text
1. ACCUMULATE, в начале обработки бара t, до Exit C chain:
   обнаружить закрытую ногу по уже материализованному переходу:

     filtered_positions[t - 1] != 0
     AND filtered_positions[t] != filtered_positions[t - 1]

   exit_idx = t
   exit_price = execution_prices[t]

   Если цикл активен:
     leg = closed_leg_trade_economics(...)
     cycle_realized_pnl_micropct += int(round(leg.net_pnl_pct, 6) * 1_000_000)

2. RESET по combined_reset_event, если есть, может обнулить цикл/накопитель.

3. NEW TRIGGER из OFF, если есть:
   накопитель нового цикла = 0.

4. EXIT C predicate chain:
   cycle_take_profit проверяется первым (§8) по cycle_realized_pnl_micropct.

5. DIAGNOSTIC SNAPSHOT:
   wakeup_cycle_realized_pnl_pct[t] = значение, против которого проверялся
   порог на этом баре: после шага 1/3, до reset из шага 4.

6. DECIDE next_pos -> filtered_positions[t + 1].
```

Закрытие причинно: `exit_price = execution_prices[t]` уже известна на обработке бара `t`, поэтому look-ahead отсутствует.

Reversal уточнение:

```text
Если на шаге 1 обнаружен reversal:
  filtered_positions[t - 1] = +1/-1
  filtered_positions[t]     = -1/+1

то сначала в накопитель засчитывается закрытая старая нога с exit_idx = t.
Затем новая открытая нога получает:

  trade_entry_idx = t
  trade_entry_pos = filtered_positions[t]

Даже если cycle_take_profit на этом же баре t срабатывает и переводит cycle в OFF
или close_position, metadata новой ноги должна быть корректной: forced-close
этой новой ноги исполнится как переход filtered_positions[t] -> filtered_positions[t+1]
и не может попасть в threshold, который сработал на t.
```

Forced close от `close_position` исполняется по `open(t+1)`, его нога закрывается переходом `filtered_positions[t] -> filtered_positions[t+1]` и будет видна только на баре `t+1` в шаге 1 следующей итерации. К этому моменту цикл уже OFF и накопитель сброшен. Поэтому PnL forced close по построению не попадает в порог, который его вызвал.

Unrealized пример:

```text
Если открытая позиция даёт +2%, но не закрыта:
  cycle_realized_pnl_micropct = 0
  cycle_take_profit не срабатывает
```

## 6. Action Mode

### 6.1 `close_position`

```text
1. cycle_take_profit срабатывает на trigger-bar t.
2. cycle -> OFF, новые входы запрещены.
3. wakeup_exit_close_triggered = 1.
4. Если позиция открыта: filtered_positions[t + 1] = 0 существующей Exit C логикой.
```

### 6.2 `block_new_entries`

```text
1. cycle_take_profit срабатывает -> ST_STOPPING.
2. Новые входы блокируются.
3. Текущая позиция НЕ закрывается самим cycle_take_profit.
4. Позиция закрывается позже stopping/opposite-flip логикой.
```

### 6.3 Trade-level reason

```text
exit_reason = "wakeup_exit_cycle_take_profit" гарантируется только для mode=close_position.
```

В `block_new_entries` сделка закрывается позже (opposite flip в `ST_STOPPING`), enrichment берёт `wakeup_exit_reason` с бара `exit_index - 1`, и trade-level `exit_reason` будет `filter_stopping_opposite_flip`. Менять enrichment в рамках этого ТЗ не требуется.

### 6.4 Семантика `wakeup_exit_close_triggered`

```text
Флаг означает "close action requested", как у прочих close_position Exit C.
```

Он не означает «реально была открытая позиция и она закрылась». Реальное закрытие проверяется отдельным assertion по `positions`, не этим флагом.

## 7. Runtime и Lite Mode

При `collect_filter_diagnostics=False`:

```text
1. Diagnostic arrays не аллоцируются.
2. filter_diagnostics is None.
3. cycle_realized_pnl_micropct ведётся локальной переменной FSM.
4. positions идентичны режиму collect_filter_diagnostics=True.
```

Поведение exit запрещено завязывать на diagnostic arrays.

## 8. Приоритет Exit C

`cycle_take_profit` — первым в elif-цепочке:

```text
1. cycle_take_profit
2. ttl
3. no_fresh_candidate
4. local_median_stop
5. max_trades_per_cycle   (внутренний FSM-reason: cycle_trade_limit)
```

Tie-break:

```text
cycle_take_profit + ttl на одном баре -> wakeup_exit_reason = "cycle_take_profit"
```

Подчиняется one-shot gate `wakeup_exit_c_fired`: `cycle_take_profit` срабатывает не более одного раза за wakeup-cycle.

## 9. Runtime trade metadata (новое состояние FSM)

Для exact PnL при закрытии нужны entry-данные открытой ноги:

```text
trade_entry_idx : int
trade_entry_pos : int
```

Обновление:

```text
open / wakeup entry:
  trade_entry_idx = opened_idx
  trade_entry_pos = opened_pos

reversal:
  1. закрыть старую ногу и зачесть её PnL;
  2. открыть новую ногу:
       trade_entry_idx = reversal_idx
       trade_entry_pos = new_pos

restore_allowed_position_on_st_flip:
  если реально открывается позиция из flat:
    trade_entry_idx = t
    trade_entry_pos = restored_pos

flat:
  trade_entry_idx = -1
  trade_entry_pos = 0

forced close:
  metadata сохраняется до материализованного перехода close на следующем баре,
  чтобы helper мог посчитать закрытую ногу; после закрытия сбрасывается.
```

## 10. Сброс накопителя

`cycle_realized_pnl_micropct -> 0` синхронно с прочими per-cycle accumulators (`cycle_age`, `cycle_trade_count`, `wakeup_exit_c_fired`) при:

```text
daily / time-session / combined reset
opposite_st_flip, завершающий cycle
close_position Exit C (cycle -> OFF) — эффект виден со следующего бара (§5)
ST_STOPPING -> OFF
новый trigger нового wakeup-cycle
```

В `block_new_entries` после срабатывания Exit C цикл неактивен, накопитель обнуляется; PnL позиции, закрывающейся позже в `ST_STOPPING`, в накопитель не возвращается.

Главное правило:

```text
PnL одного wakeup-cycle не должен перетекать в следующий wakeup-cycle.
```

## 11. Diagnostics

Новые per-bar поля, только при `collect_filter_diagnostics=True`:

```text
wakeup_cycle_realized_pnl_pct            float, fraction; snapshot по §5
wakeup_exit_cycle_take_profit_triggered  int8
```

Расширить существующие значения:

```text
wakeup_exit_reason     += "cycle_take_profit"
wakeup_position_action += "exit_cycle_take_profit"
```

Маппинги:

```text
_wakeup_exit_action_for_reason:
  "cycle_take_profit" -> "exit_cycle_take_profit"

_wakeup_exit_reason_at:
  "cycle_take_profit" -> "wakeup_exit_cycle_take_profit"

_wakeup_position_action_at:
  "exit_cycle_take_profit" -> "wakeup_exit_cycle_take_profit"
```

## 12. Summary / Counters

Добавить counter:

```text
wakeup_exit_cycle_take_profit_count =
  sum(wakeup_exit_cycle_take_profit_triggered)
```

Counter должен быть определён в обоих builder'ах с идентичной семантикой:

```text
1. tester:
   testing/runner.py :: _build_wakeup_summary_fields
   Рабочий путь.

2. WF/Grid:
   wf_grid/wf/step_executor.py :: _compute_filter_diagnostics_summary
   Parity/dead-code path, потому что фича gated tester-only.
```

WF/Grid summary builder не должен выводить wakeup semantics из config. Он должен просто считать ключ `wakeup_exit_cycle_take_profit_triggered`, если этот ключ присутствует в `filter_diagnostics`; если ключа нет — counter отсутствует или равен `0` по существующему локальному стилю summary.

Export-строка рядом с прочими wakeup exits:

```text
Wakeup Exit Cycle Take Profit
```

Не повторять дыру `cycle_trade_limit`: bar-level array есть, summary-counter нет. Для `cycle_take_profit` counter обязателен.

## 13. Plumbing: сигнатура `apply()`

```text
apply(..., commission_rate: float | None = None)
```

Правила:

```text
commission_rate обязателен только когда cycle_take_profit.enabled is True.
Если cycle_take_profit.enabled is True и commission_rate is None:
  явная ошибка RuntimeError/ValueError с понятным сообщением.

При выключенной фиче commission_rate может быть None.
Прямые unit-тесты apply() без фичи остаются совместимыми.

open_prices переквалифицируется из "not used" в реально используемый параметр
при cycle_take_profit.enabled.

backtest.py должен прокинуть commission_rate в _zz_apply.
```

## 14. Валидация конфига

Добавить config object:

```text
TradeFilterWakeupCycleTakeProfitExitConfig
  enabled: object = False
  pnl_pct: object = None
```

Правила:

```text
cycle_take_profit.enabled must be bool
cycle_take_profit.pnl_pct is required when enabled=true
cycle_take_profit.pnl_pct must be number
cycle_take_profit.pnl_pct must be > 0
cycle_take_profit засчитывается как enabled exit condition
```

Allowlists:

```text
donor trade_filter_config.py:
  whitelist + _validate_wakeup_regime_block

wf_grid loader.py:
  trade_filter.wakeup_regime.exit:
    + "cycle_take_profit"

  trade_filter.wakeup_regime.exit.cycle_take_profit:
    {"enabled", "pnl_pct"}
```

Валидно:

```yaml
pnl_pct: 0.01
pnl_pct: 0.005
pnl_pct: 0.025
```

Невалидно:

```yaml
pnl_pct: 0
pnl_pct: -0.01
pnl_pct: "1%"
pnl_pct: null   # при enabled=true
```

Если `cycle_take_profit.enabled=false`, отсутствие `pnl_pct` допустимо.

WF/Grid-конфиг с `cycle_take_profit` обязан reject'иться через существующий gate (`wakeup_regime_unsupported_pipeline`).

## 15. Определение cycle membership для AT#15

Закрытая сделка относится к накопителю цикла тогда и только тогда, когда её закрытие учтено в `cycle_realized_pnl_micropct` на шаге 1 §5, пока цикл был активен.

Формально:

```text
membership =
  trades с exit_idx в диапазоне закрытий активного wakeup-cycle,
  учтённые при active cycle runtime.
```

Исключаются:

```text
forced-close нога close_position:
  exit_idx = trigger_bar + 1
  закрытие после OFF

позиция, закрывающаяся в ST_STOPPING при block_new_entries:
  цикл уже неактивен
```

Тест сверяет:

```text
cycle_realized_pnl_micropct
==
sum(int(round(trades_df.net_pnl_pct, 6) * 1_000_000) for trades in membership)
```

Сверка — integer micro-percent, без float tolerance.

## 16. Acceptance Tests

```text
1.  enabled=false сохраняет старое поведение.
2.  pnl_pct=0.01, closed +0.0099 -> НЕ срабатывает.
3.  pnl_pct=0.01, closed +0.0100 -> срабатывает.
4.  pnl_pct=0.01, closed +0.0120 -> срабатывает один раз, cycle -> OFF.
5.  Unrealized открытой позиции не влияет на threshold.
6.  Новый wakeup-cycle: накопитель = 0.
7.  close_position:
    wakeup_exit_close_triggered=1 (action-requested, §6.4);
    отдельный ассерт: positions реально ушли в 0, если позиция была.
8.  block_new_entries:
    cycle_take_profit НЕ закрывает текущую позицию.
9.  cycle_take_profit + ttl одновременно:
    reason == "cycle_take_profit".
10. Diagnostics содержат wakeup_cycle_realized_pnl_pct;
    на trigger-bar показывают значение, достигшее порога
    (snapshot до reset, §5).
11. Summary содержит wakeup_exit_cycle_take_profit_count
    в tester и parity в WF/Grid builder.
12. Trade enrichment:
    exit_reason="wakeup_exit_cycle_take_profit" только close_position.
13. collect_filter_diagnostics=False:
    filter_diagnostics is None.
14. collect_filter_diagnostics=False:
    positions идентичны режиму True.
15. STRICT:
    cycle_realized_pnl_micropct ==
    sum(round(net_pnl_pct, 6) * 1e6) по membership §15.
    Покрыть flat-to-flat и reversal. Без float tolerance.
16. Reversal close/open:
    закрытая нога учитывается с F-16 split, идентично trades_df.
17. Forced close (close_position) НЕ участвует в пороге,
    вызвавшем этот forced close.
18. one-shot:
    <= 1 срабатывание за цикл (wakeup_exit_c_fired).
19. REFACTOR-GUARD:
    golden/reference fixtures подтверждают, что trades_df после выделения
    helper остаётся идентичным для flat/reversal/pending/reset сценариев.
20. apply() без фичи работает с commission_rate=None;
    enabled=true и commission_rate=None -> явная ошибка.
21. WF/Grid REJECT:
    конфиг с cycle_take_profit отклоняется;
    wakeup_regime_unsupported_pipeline сохранён.
22. Mode-D diagnostics keyset:
    добавлены ровно 2 новых ключа, остальные не изменились.
```

## 17. Итог

`cycle_take_profit` выключает wakeup-cycle при достижении fraction-порога суммой realized net PnL закрытых сделок цикла. Реализация — exact-inline накопитель внутри `apply()` с causal-индексацией: закрытие детектируется в начале бара по уже материализованному переходу `positions[t-1] -> positions[t]`.

Bit-for-bit сверка с `trades_df["net_pnl_pct"]` достигается единым helper'ом `closed_leg_trade_economics` и integer micro-percent арифметикой. Фича tester-only: WF/Grid gate сохранён, правки там только parity/dead-code. `commission_rate` в `apply()` опционален и обязателен лишь при включённой фиче. Snapshot диагностики пишется до reset. Forced close по построению не влияет на собственный trigger.

## 18. Явные решения вне ТЗ

```text
Снятие/изменение gate wakeup_regime_unsupported_pipeline
для реальной поддержки WF/Grid.

Перевод wakeup_exit_close_triggered на actual-close semantics.
Сейчас сохранена action-requested semantics ради совместимости.
```
