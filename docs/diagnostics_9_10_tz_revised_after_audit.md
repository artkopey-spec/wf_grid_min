Ниже исправленная версия ТЗ с учётом аудита. Я убрал скрытые противоречия, зафиксировал скоуп `pr_100`, развёл фазы, сохранил принцип “движок не трогаем” и добавил недостающие инженерные контракты.

**ТЗ: Диагностика Прогона 9/10, ревизия после аудита**

**0. Главный принцип**

Диагностика строится как аналитический слой поверх результатов прогона.

Движок не трогаем.  
Не меняем `runner.py`, `signal_events.py`, логику фильтра и расчёт сделок.

Все новые аналитические метрики считаются в слое экспорта из уже доступных данных:

`pr_100`, `df`, `trades_df`, `signals_df`, `fd_100`, `filters_summary`, `cycle`, `run_metadata`.

Если метрика не может быть честно посчитана из этих данных, она либо помечается как proxy, либо переносится в отдельную будущую задачу.

---

**1. Скоуп расчётов**

Все новые аналитические листы Фазы A/B/C строятся только по `100%`-срезу, то есть по `pr_100`.

Мульти-периодная аналитика `75/50/33/25%` не входит в это ТЗ.

Это фиксирует единый источник чисел для:

`Dashboard`, `Run_Health`, `Trade_Analytics`, `Equity_Drawdown`, `Filter_Funnel`, `Filter_Attribution`, `Cycle_Summary`, `Cost_Sensitivity`, `Returns_Calendar`, `Robustness`.

---

**2. Обратная совместимость и гейтинг**

Контракт `disabled path bit-identical` сохраняется.

Правило:

- если фильтр/расширенная диагностика выключены, новые аналитические листы не пишутся;
- существующий набор листов и порядок колонок в disabled-path остаётся бит-идентичным baseline;
- новые листы включаются только при активном diagnostic/export-gate.

Нужен единый флаг-гейт:

```text
export_diagnostics_v2=True
```

И дочерние флаги:

```text
export_dashboard
export_run_health
export_trade_analytics
export_equity_drawdown
export_filter_funnel
export_filter_attribution
export_cycle_summary
export_cost_sensitivity
export_remediation
export_returns_calendar
export_robustness
```

По умолчанию новые листы включены только в compatible-enabled режиме, но не в disabled baseline path.

---

**3. Финальная структура книги**

Старые листы оставить:

`Tester_Config`, `Summary`, `Metrics_100`, `Trades_100`, `Signals`, `FilterDiagnostics_100`, `ZigZag_Trigger_Events`, `filters_summary`, `cycle`.

Новые листы:

| Фаза | Лист | Назначение |
|---|---|---|
| A | `Index` | навигация |
| A | `Reproducibility` | commit/config/data/runtime metadata |
| A | `Dashboard` | главный вердикт |
| A | `Run_Health` | проверяемые инварианты |
| A | `Trade_Analytics` | MFE/MAE/giveback/entry-exit quality |
| A | `Equity_Drawdown` | equity-by-trade, underwater, DD episodes |
| A | `Filter_Funnel` | честная атрибуция прохождения/блокировок |
| A | `Filter_Attribution` | proxy forward-return по blocked/allowed |
| A | `Cycle_Summary` | циклы + trade number in cycle |
| A | `Cost_Sensitivity` | commission/slippage stress в bps |
| A | `Remediation` | симптом → причина → параметр |
| B | `Returns_Calendar` | месяц/день/час/день недели |
| B | `Exit_Quality` | агрегаты по exit reason + post-exit proxy |
| B | `False_Start_2.0` | эволюция существующего false-start листа |
| B | `Data_Dictionary` | колонки, формулы, единицы |
| C | `Robustness` | bootstrap/Monte Carlo/significance |
| C | `FilterDiagnostics_sampled` | облегчённый raw diagnostics |

`Trade_Number_In_Cycle` не делать отдельным листом. Это блок внутри `Cycle_Summary`.

`False_Start_2.0` не делать вторым конкурирующим листом. Это замена/расширение существующего false-start листа под тем же гейтом.

---

**4. Dashboard Фазы A**

Dashboard Фазы A показывает только то, что уже считается в Фазе A.

KPI:

| KPI | Источник |
|---|---|
| Net PnL | `trades_df` / `Summary` |
| MaxDD by trade-equity | `Equity_Drawdown` |
| Profit Factor | `trades_df` |
| Avg Trade | `trades_df` |
| Median Trade | `trades_df` |
| Expectancy | `trades_df` |
| Win Rate | `trades_df` |
| False Start % | false-start diagnostics |
| Exposure % | `fd_100`, если доступно |
| Filter ON % | `fd_100` / `filters_summary` |
| Breakeven commission | `Cost_Sensitivity`, считается в Фазе A |
| Cost fragility flag | `Cost_Sensitivity` |

`Mean trade CI`, p-value и `not_significant` переносятся в Фазу C вместе с `Robustness`.

---

**5. Run_Health: только проверяемые инварианты**

Запрещено ставить PASS по тому, что нельзя доказать из выходных данных.

Проверки:

| Check | Условие |
|---|---|
| `Summary vs Trades` | Sum PnL совпадает |
| `Entries Allowed vs Trades` | сходится или есть documented exception |
| `Filter states sum` | сумма состояний равна числу баров |
| `Warmup respected` | entry_index >= warmup |
| `Duplicate timestamps` | отсутствуют |
| `OHLCV NaN` | посчитаны и явно показаны |
| `Timezone consistency` | единая TZ или explicit timezone-naive |
| `Trade index bounds` | entry/exit корректно клиппятся к `len(df)` |
| `Signal before entry` | если есть signal index, entry не раньше signal |
| `Execution price sanity` | entry/exit price согласуется с доступными OHLC в допустимом режиме |
| `Commission sanity` | commission=0 даёт WARN |
| `Cycle map coverage` | доля сделок, привязанных к циклам |

`Lookahead-risk` переименовать в `Execution ordering sanity`.  
Полный lookahead-аудит кода не входит в Excel-диагностику.

---

**6. Reproducibility**

Экспортёр не собирает git/hash сам. Он только отображает `run_metadata`.

Сбор делается уровнем CLI/runner-wrapper.

Поля:

| Поле | Источник |
|---|---|
| git commit | `run_metadata` |
| branch | `run_metadata` |
| dirty worktree flag | `run_metadata` |
| config hash | `run_metadata` |
| data file path | `run_metadata` |
| data hash | `run_metadata`, optional/cached |
| rows count | `df` |
| first/last timestamp | `df.index` |
| timezone | `df.index` |
| report generator version | exporter constant |
| run started/finished | `run_metadata` |
| Python/pandas/openpyxl versions | `run_metadata` or exporter |
| command line / entrypoint | `run_metadata` |

Если поле отсутствует, писать `missing`, не пытаться угадывать.

---

**7. T+N forward returns без правки движка**

`T+5` и `T+10` не добавлять в `signal_events.py`.

В экспорте сделать общий util:

```text
compute_forward_returns(df, event_index, horizons=(1, 3, 5, 10), price_col="close")
```

Он используется в:

- `Filter_Attribution`;
- `Exit_Quality`;
- `False_Start_2.0`.

Поведение:

- если `event_index + horizon >= len(df)`, значение `NaN`;
- расчёт только по `close`;
- явно пометить как fixed-horizon proxy, не как реальная сделка.

---

**8. Filter_Funnel без ложной последовательности**

Не показывать `% от прошлого`, если реальные гейты не являются каскадом.

Формат Фазы A:

| Gate / Reason | Eligible bars | Pass count | Fail count | Pass rate | Notes |
|---|---:|---:|---:|---:|---|
| Time window |
| Candidate height |
| ATR |
| Volume |
| Trade mode |
| Filter ON |
| Wakeup all OK |
| Entry allowed |
| Trade opened |

Если из кода можно восстановить фактический порядок первого блокирующего гейта, добавить блок:

```text
First blocking reason attribution
```

Но не выдавать независимые флаги за последовательную воронку.

---

**9. Filter_Attribution как proxy, не PnL-факт**

Переименовать смысл метрик:

Вместо:

```text
Saved PnL
Lost PnL
Net filter value
```

Использовать:

```text
Blocked negative forward return proxy
Blocked positive forward return proxy
Net forward-return proxy
Block directional quality
Missed opportunity proxy
```

Таблица:

| Category | Count | T+1 mean | T+3 mean | T+5 mean | T+10 mean | Positive % | Negative % |
|---|---:|---:|---:|---:|---:|---:|---:|
| Allowed entries |
| Blocked by filter_off |
| Blocked by time reset |
| Blocked by daily reset |
| Blocked by ATR |
| Blocked by volume |
| Blocked by candidate |
| Blocked by trade mode |

Примечание на листе: это fixed-horizon counterfactual proxy, а не реализуемый PnL.

---

**10. Trade_Analytics**

Единый источник per-trade метрик.

Поля:

| Поле | Формула / правило |
|---|---|
| MFE % | max favorable move на `[entry_index, clipped_exit_index]` |
| MAE % | max adverse move на том же диапазоне |
| Edge ratio | `MFE / abs(MAE)` |
| R-multiple | `PnL / abs(MAE)` |
| Time to MFE | бар максимума MFE относительно entry |
| Time to MAE | бар максимума MAE относительно entry |
| Giveback | `MFE - final PnL` |
| Exit efficiency | `final PnL / MFE`, если MFE != 0 |
| Cycle ID | из `derive_trade_cycle_map` |
| Trade # in cycle | из `derive_trade_cycle_map` |
| Regime at entry | из `fd_100` по `entry_index` |
| Hour/day/month | из timestamp entry |

Для `exit_index >= len(df)`:

```text
clipped_exit_index = len(df) - 1
is_clipped_exit = True
```

Такие сделки помечаются, а MFE/MAE считаются только на доступной части данных.

---

**11. Cycle mapping: один общий util**

Добавить единый расчёт:

```text
derive_trade_cycle_map(fd_100, trades_df) -> DataFrame
```

Возвращает:

```text
trade_id
cycle_id
trade_idx_in_cycle
cycle_age_at_entry
cycle_trade_count_at_entry
is_in_cycle
cycle_start_index
cycle_end_index
mapping_status
```

Правило:

- цикл начинается на rising edge активного wakeup/cycle режима;
- цикл заканчивается на falling edge либо reset/end-state;
- сделка привязывается к циклу по `entry_index`;
- если `entry_index` вне активного цикла, `is_in_cycle=False`, `cycle_id=NaN`;
- нумерация сделок внутри цикла считается только этим util, не дублируется в листах.

`Trade_Analytics` и `Cycle_Summary` используют только этот результат.

---

**12. Equity_Drawdown**

Основной equity — `trade-equity`.

Явно указать на листе:

```text
Trade-equity DD does not include intratrade mark-to-market drawdown.
```

Метрики:

| Блок | Содержимое |
|---|---|
| Equity by trade | cumulative net PnL |
| Underwater by trade | equity - running max |
| DD episodes | start, bottom, recovery |
| Worst 10 DD | depth, duration, recovery |
| Recovery stats | avg/max recovery length |

Если движковый MaxDD считается иначе, в `Run_Health` не требовать полного равенства. Показывать как отдельные определения.

---

**13. Cost_Sensitivity**

Slippage считать в bps/процентах, не в тиках.

Сценарии:

| Scenario | Unit |
|---|---|
| commission 0 bps | bps per side |
| commission 0.5 bps | bps per side |
| commission 1 bps | bps per side |
| commission 2 bps | bps per side |
| slippage 0.5 bps | bps per side |
| slippage 1 bps | bps per side |
| commission + slippage | bps per side |

Tick slippage разрешён только если `tick_size` явно передан в `run_metadata`.

Все метрики пересчитываются из per-trade PnL.  
Формула Sharpe/MaxDD на листе должна быть явно указана как `per-trade`.

Добавить сверку:

```text
actual-cost recomputed metrics vs Metrics_100
```

Если определения различаются, статус `WARN`, не `FAIL`.

---

**14. Remediation**

Лист остаётся P0, потому что у него максимальный ROI.

Формат:

| Symptom | Detection metric | Likely cause | Parameter family | Suggested action |
|---|---|---|---|---|
| false-start high | false-start % > threshold | noise entries | candidate/reversal thresholds | tighten confirmation |
| giveback high | avg giveback > threshold | late exit | TTL/exit mode/trailing | reduce giveback |
| edge decays by trade # | trade_idx_in_cycle N+ negative | overtrading | cycle trade limit | lower max trades |
| filter blocks positive proxy | blocked T+N positive high | filter too strict | ATR/volume/candidate | relax filter |
| bad hours | hour bucket PnL negative | time regime | time filter | exclude weak hours |
| cost fragile | breakeven bps too low | no edge buffer | execution/frequency | reduce churn |

---

**15. Threshold config**

Все цветовые флаги должны идти из одной таблицы-константы.

Минимум:

| Flag | Default |
|---|---:|
| `pf_weak` | PF < 1.1 |
| `median_negative` | median trade < 0 |
| `false_start_high` | false-start % > 30% |
| `avg_trade_too_small` | avg trade <= estimated round-trip cost |
| `cost_fragile` | stressed PnL loss > 50% |
| `low_filter_coverage` | Filter ON % < 15% |
| `dd_duration_high` | recovery duration > configurable bars |
| `cycle_overtrade` | trade # N median PnL < 0 |

Единицы хранить явно: pct, bps, bars, trades.

---

**16. Нефункциональные требования**

Чтобы Excel не стал неподъёмным:

| Требование | Значение |
|---|---|
| новые листы | агрегированные, не дублируют `FilterDiagnostics_100` |
| raw diagnostics | можно отключить отдельным флагом |
| `FilterDiagnostics_sampled` | поднять в Фазу A как lightweight-альтернативу |
| export time budget | зафиксировать baseline + допустимый overhead |
| file size budget | зафиксировать baseline + допустимый overhead |
| memory | избегать дополнительных копий 297k×78 без необходимости |

---

**17. Целевой код и дедупликация**

Целевой первичный модуль:

```text
donor/supertrend_optimizer/io/excel_tester.py
```

Новые расчёты выносить в отдельный общий модуль, например:

```text
diagnostics_v2.py
```

или локальный блок build-utils, если проект пока не готов к выделению.

Синхронизировать или явно запланировать парити для:

```text
donor zigzag/supertrend_optimizer/io/excel_tester.py
wf_grid/export/xlsx_writer.py
```

Нужен тест-парити либо документированное решение, какой экспортёр является canonical.

---

**18. Фазы и Acceptance Criteria**

**Фаза A: максимальный ROI**

Делает:

`Index`, `Reproducibility`, `Dashboard`, `Run_Health`, `Trade_Analytics`, `Equity_Drawdown`, `Filter_Funnel`, `Filter_Attribution`, `Cycle_Summary`, `Cost_Sensitivity`, `Remediation`, lightweight `FilterDiagnostics_sampled`.

Фаза A должна отвечать:

1. Есть ли edge после комиссии?
2. Сходятся ли основные инварианты?
3. Где была max trade-equity просадка и сколько длилось восстановление?
4. Качественные ли входы/выходы по MFE/MAE/giveback?
5. Фильтр скорее помогает или мешает по forward-return proxy?
6. Есть ли переторговка внутри цикла?
7. Какая комиссия/slippage убивает edge?
8. Какой параметр вероятнее всего чинить?

**Фаза B: аналитическая детализация**

Делает:

`Returns_Calendar`, `Exit_Quality`, `False_Start_2.0`, расширенный `Data_Dictionary`.

Фаза B добавляет ответы:

1. Какие часы/дни/месяцы портят результат?
2. Какой exit reason зарабатывает/теряет?
3. Какие типы false-start доминируют?
4. Что происходит после выхода на T+1/T+3/T+5/T+10 proxy?

**Фаза C: устойчивость**

Делает:

`Robustness`, bootstrap, Monte Carlo, CI средней сделки, p-value/proxy significance.

Фаза C добавляет ответы:

1. Значима ли средняя сделка?
2. Насколько результат зависит от порядка сделок?
3. Есть ли overfit/tail-risk признаки?
4. Держится ли edge статистически, а не только визуально?

---

**19. Definition of Done**

Для каждого нового листа:

- есть `_build_<sheet>_df(...)`;
- есть `_write_<sheet>_sheet(...)`;
- есть `_empty_<sheet>_df(...)`;
- описаны колонки, формулы, единицы;
- есть поведение на пустых/частичных данных;
- есть unit-тест build-функции;
- disabled-path остаётся bit-identical;
- новые листы не требуют изменения движка;
- T+N считается в экспорте;
- cycle mapping считается одной общей функцией;
- proxy-метрики явно подписаны как proxy.

Итоговая формула ТЗ теперь такая:

`pr_100 only → preserve disabled baseline → reproducibility from metadata → health checks only on verifiable facts → trade quality from df/trades → filter value as proxy → cycle mapping via one util → cost in bps → phased AC`.

Так ТЗ остаётся сильным продуктово, но становится реализуемым без скрытых архитектурных мин.
