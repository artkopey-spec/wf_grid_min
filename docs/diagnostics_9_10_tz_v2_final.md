# ТЗ v2: диагностика прогона 9/10

## 0. Назначение

Документ задает финальный контракт реализации расширенной Excel-диагностики для одиночного tester-прогона.

Цель диагностики - дать проверяемый аналитический слой поверх уже полученных результатов backtest/tester-прогона:

- быстро понять, есть ли edge после комиссии;
- проверить базовые инварианты результата;
- оценить качество входов и выходов;
- оценить вклад фильтра только как proxy, не как доказанный PnL;
- показать циклы, переторговку, просадки и чувствительность к издержкам;
- сохранить обратную совместимость disabled-path.

Диагностика не меняет торговый движок и не меняет результат сделок.

## 1. Главные ограничения

### 1.1. Движок не трогаем

Запрещено менять:

- `runner.py`;
- `signal_events.py`;
- логику фильтра;
- расчет позиций;
- расчет сделок;
- расчет engine-метрик.

Все новые метрики строятся в export/diagnostics layer из уже доступных данных.

### 1.2. Canonical exporter

Canonical target для этого ТЗ:

```text
donor/supertrend_optimizer/io/excel_tester.py
```

Расчеты v2 должны быть вынесены из exporter-монолита в отдельный модуль или пакет, например:

```text
donor/supertrend_optimizer/io/diagnostics_v2.py
```

`excel_tester.py` должен только:

- собрать diagnostic context;
- вызвать build-функции;
- записать DataFrame на листы;
- применить минимальное форматирование Excel.

Не входит в это ТЗ:

- реализация v2-листов в `wf_grid/export/xlsx_writer.py`;
- реализация v2-листов в `donor zigzag/supertrend_optimizer/io/excel_tester.py`.

Для этих exporter'ов требуется отдельная задача parity/rollout.

### 1.3. Старые листы сохраняются

В enabled и disabled режимах все существующие legacy-листы остаются в прежнем контракте.

Старые multi-period листы не удаляются:

```text
Metrics_100, Metrics_75, Metrics_50, Metrics_33, Metrics_25
Trades_100, Trades_75, Trades_50, Trades_33, Trades_25
```

Новые аналитические листы v2 строятся только по `100%`-срезу (`pr_100`).

### 1.4. Disabled-path bit-identical

Если фильтр выключен или v2 diagnostics выключены, disabled baseline path остается bit-identical:

- не добавляются новые v2-листы;
- не меняется порядок существующих листов;
- не меняется порядок колонок existing sheets;
- не добавляются новые строки в existing sheets;
- не меняются значения existing cells;
- не меняется timestamp/metadata поведение существующего exporter'а.

## 2. Источники данных

Новые v2-метрики можно считать только из:

```text
period_results
pr_100
df
trades_100
signals_df
fd_100
filter_diagnostics_summary
run_metadata
trade_filter_config
config_yaml_snapshot
```

Где:

- `pr_100` - `PeriodResult` с `period_label == "100%"`;
- `trades_100` - `pr_100.trades_df`;
- `fd_100` - `pr_100.filter_diagnostics`;
- `filter_diagnostics_summary` - `pr_100.filter_diagnostics_summary`;
- `df` - исходный OHLCV DataFrame, переданный в exporter;
- `signals_df` - уже построенный DataFrame signal events, если он был передан;
- `run_metadata` - metadata, собранная CLI/wrapper-слоем.

Если метрика не может быть честно посчитана из этих данных:

- она получает статус `SKIP` или `missing`;
- либо явно маркируется как `proxy`;
- либо выносится из текущей реализации.

Запрещено в exporter'е заново запускать backtest, пересчитывать позиции или вызывать signal engine.

## 3. Diagnostic Context

Перед построением листов создается единый внутренний объект:

```text
DiagnosticsV2Context
```

Минимальные поля:

```text
period_results
pr_100
df
trades_100
signals_df
fd_100
filter_diagnostics_summary
run_metadata
trade_filter_config
config_yaml_snapshot
cycle_map
thresholds
```

Контекст отвечает за:

- поиск `pr_100`;
- нормализацию пустых DataFrame;
- проверку наличия колонок;
- построение `cycle_map`;
- доступ к threshold constants;
- единое поведение на missing/partial data.

Build-функции листов не должны самостоятельно искать `pr_100` или повторно строить общие производные таблицы.

## 4. Гейтинг

### 4.1. Существующие гейты

Существующий exporter уже имеет флаги:

```text
export_diagnostics
export_signals
export_false_start
export_cycle
export_trades
```

Существующий filter config уже имеет:

```text
trade_filter.enabled
trade_filter.diagnostics.export_state_columns
trade_filter.diagnostics.export_trigger_columns
```

Эти флаги сохраняются без изменения семантики.

### 4.2. Новый v2-гейт

Добавить верхнеуровневый аргумент exporter'а:

```text
export_diagnostics_v2: bool = False
```

Значение по умолчанию `False`, чтобы существующие вызовы сохраняли старый workbook.

Новые v2-листы пишутся только если одновременно:

```text
filter_enabled == True
export_diagnostics == True
export_diagnostics_v2 == True
pr_100 exists
```

`filter_enabled` означает:

```text
is_zigzag_enabled(trade_filter_config) or is_volume_enabled(trade_filter_config)
```

### 4.3. Дочерние v2-флаги

Добавить опциональный mapping:

```text
diagnostics_v2_flags: dict[str, bool] | None = None
```

Если `export_diagnostics_v2=True`, дочерние флаги по умолчанию включены для Фазы A и выключены для Фаз B/C.

Фаза A defaults:

```text
export_index=True
export_reproducibility=True
export_dashboard=True
export_run_health=True
export_trade_analytics=True
export_equity_drawdown=True
export_filter_funnel=True
export_filter_attribution=True
export_cycle_summary=True
export_cost_sensitivity=True
export_remediation=True
export_filter_diagnostics_sampled=True
```

Фаза B defaults:

```text
export_returns_calendar=False
export_exit_quality=False
export_false_start_2=False
export_data_dictionary=False
```

Фаза C defaults:

```text
export_robustness=False
```

Если дочерний флаг выключен, соответствующий лист не пишется.

### 4.4. Матрица гейтов

| Условие | Legacy sheets | Existing diagnostics sheets | v2 sheets |
|---|---|---|---|
| filter disabled | без изменений | не пишутся | не пишутся |
| filter enabled, `export_diagnostics=False` | без изменений | не пишутся | не пишутся |
| filter enabled, `export_diagnostics=True`, `export_diagnostics_v2=False` | без изменений | как сейчас | не пишутся |
| filter enabled, `export_diagnostics=True`, `export_diagnostics_v2=True` | без изменений | как сейчас | пишутся по дочерним флагам |

`cycle` остается existing diagnostics sheet и управляется существующим `export_cycle`.

`Cycle_Summary` является v2-листом и управляется `export_diagnostics_v2` + `export_cycle_summary`.

## 5. Порядок листов

Disabled path сохраняет прежний порядок листов.

Enabled v2 path:

1. сначала пишутся existing legacy sheets в текущем порядке exporter'а;
2. затем existing optional diagnostics sheets в текущем порядке exporter'а;
3. затем v2-листы в фиксированном порядке:

```text
Index
Reproducibility
Dashboard
Run_Health
Trade_Analytics
Equity_Drawdown
Filter_Funnel
Filter_Attribution
Cycle_Summary
Cost_Sensitivity
Remediation
FilterDiagnostics_sampled
Returns_Calendar
Exit_Quality
False_Start_2
Data_Dictionary
Robustness
```

Листы Фаз B/C пишутся только если включены их дочерние флаги.

`Index` не должен становиться первым листом workbook, чтобы не ломать потребителей, ожидающих текущий первый лист.

## 6. Статусы диагностики

Все проверочные листы используют единый status vocabulary:

```text
PASS
WARN
FAIL
SKIP
INFO
```

Правила:

- `PASS` - проверка доказуема из доступных данных и выполнена;
- `WARN` - проверка выявила риск или несовпадение определений, но это не invalidates run;
- `FAIL` - нарушен проверяемый инвариант данных;
- `SKIP` - не хватает источников или колонок;
- `INFO` - информационный факт без pass/fail смысла.

Запрещено ставить `PASS` по проверке, которую нельзя доказать из текущих output data.

## 7. Фаза A: обязательный состав

Фаза A является целевым объемом первой реализации.

Листы Фазы A:

```text
Index
Reproducibility
Dashboard
Run_Health
Trade_Analytics
Equity_Drawdown
Filter_Funnel
Filter_Attribution
Cycle_Summary
Cost_Sensitivity
Remediation
FilterDiagnostics_sampled
```

Фаза A должна ответить:

1. Есть ли edge после комиссии?
2. Сходятся ли проверяемые инварианты?
3. Где была максимальная trade-equity просадка?
4. Сколько длилось восстановление после просадки?
5. Качественные ли входы/выходы по MFE/MAE/giveback?
6. Фильтр скорее помогает или мешает по fixed-horizon proxy?
7. Есть ли переторговка внутри цикла?
8. Какая комиссия/slippage убивает edge?
9. Какой параметр вероятнее всего чинить первым?

## 8. Index

Лист `Index` содержит навигацию по v2-листам.

Колонки:

```text
Sheet
Phase
Purpose
Status
Primary source
Notes
```

`Status`:

- `present`, если лист записан;
- `disabled`, если дочерний флаг выключен;
- `skipped`, если не хватает данных.

## 9. Reproducibility

Exporter не собирает git/hash сам.

Exporter только отображает `run_metadata` и добавляет собственную константу:

```text
report_generator_version
```

Поля:

| Field | Source | Missing behavior |
|---|---|---|
| git commit | `run_metadata` | `missing` |
| branch | `run_metadata` | `missing` |
| dirty worktree flag | `run_metadata` | `missing` |
| config hash | `run_metadata` | `missing` |
| data file path | `run_metadata` | `missing` |
| data hash | `run_metadata` | `missing` |
| rows count | `df` | `missing` |
| first timestamp | `df.index` | `missing` |
| last timestamp | `df.index` | `missing` |
| timezone | `df.index` | `timezone-naive` или `missing` |
| report generator version | exporter constant | required |
| run started | `run_metadata` | `missing` |
| run finished | `run_metadata` | `missing` |
| Python version | `run_metadata` | `missing` |
| pandas version | `run_metadata` | `missing` |
| openpyxl version | `run_metadata` | `missing` |
| command line / entrypoint | `run_metadata` | `missing` |

Exporter не должен угадывать отсутствующие metadata.

## 10. Dashboard

Dashboard показывает только метрики, которые уже построены в Фазе A.

Колонки:

```text
KPI
Value
Unit
Status
Source sheet
Notes
```

KPI:

| KPI | Source | Rule |
|---|---|---|
| Net PnL | `trades_100.net_pnl_pct` | sum |
| Profit Factor | `trades_100.net_pnl_pct` | gross profit / abs(gross loss) |
| Avg Trade | `trades_100.net_pnl_pct` | mean |
| Median Trade | `trades_100.net_pnl_pct` | median |
| Expectancy | `trades_100.net_pnl_pct` | mean, same unit as trade PnL |
| Win Rate | `trades_100.net_pnl_pct` | percent of trades > 0 |
| MaxDD by trade-equity | `Equity_Drawdown` | worst underwater |
| False Start % | existing false-start data if available | otherwise `SKIP` |
| Exposure % | `fd_100` if derivable | otherwise `SKIP` |
| Filter ON % | `fd_100` / summary | active filter state bars / total bars |
| Breakeven commission | `Cost_Sensitivity` | bps per side |
| Cost fragility flag | `Cost_Sensitivity` | threshold-based |

Statistical significance, p-value, confidence interval and `not_significant` do not appear in Phase A Dashboard. They belong to `Robustness`.

## 11. Run_Health

Run_Health содержит только проверяемые инварианты.

Колонки:

```text
Check
Status
Observed
Expected
Tolerance
Source
Notes
```

Проверки:

| Check | Status rule |
|---|---|
| Summary vs Trades | `PASS`, если `sum(trades_100.net_pnl_pct)` совпадает с `pr_100.metrics["sum_pnl_pct"]` в tolerance |
| Filter states length | `PASS`, если все массивы `fd_100` имеют длину `len(df)` или документированную длину result positions |
| Filter states sum | `PASS`, если histogram состояний суммируется в длину state array |
| Duplicate timestamps | `PASS`, если `df.index` не имеет дублей |
| OHLCV NaN | `WARN`, если NaN есть в OHLCV; `PASS`, если нет |
| Timezone consistency | `PASS`, если индекс единообразно tz-aware или единообразно timezone-naive |
| Trade index bounds | `PASS`, если entry/exit индексы числовые и могут быть clipped к `[0, len(df)-1]` |
| Signal before entry | `PASS`, только если есть сопоставимый signal index; иначе `SKIP` |
| Execution price sanity | `PASS`, только если можно проверить против OHLC с учетом execution model; иначе `SKIP` |
| Commission sanity | `WARN`, если комиссия равна 0; иначе `INFO/PASS` |
| Cycle map coverage | `INFO/WARN` по доле сделок, привязанных к cycle map |
| Warmup semantics | `INFO`: показать requested/effective warmup и min entry index; не FAIL, потому что текущий engine не фильтрует trades по warmup |

Запрещено требовать `entry_index >= warmup` как инвариант.

`Execution ordering sanity` заменяет любые заявления о полном lookahead-аудите. Полный lookahead-аудит кода не входит в Excel-диагностику.

## 12. Forward returns util

Добавить общий util в diagnostics layer:

```text
compute_forward_returns(df, event_index, horizons=(1, 3, 5, 10), price_col="close")
```

Поведение:

- расчет только по `df[price_col]`;
- default `price_col="close"`;
- если `event_index` отсутствует, не integer или вне диапазона, вернуть NaN по всем horizons;
- если `event_index + horizon >= len(df)`, вернуть NaN;
- return в процентах:

```text
(close[event_index + horizon] - close[event_index]) / close[event_index] * 100
```

Для short/long directional interpretation этот util не используется. Это raw fixed-horizon close-to-close proxy.

Все листы, использующие util, должны явно писать:

```text
fixed-horizon close-to-close proxy, not realized PnL
```

## 13. Filter_Funnel

Filter_Funnel не является последовательной воронкой, если реальные gate'ы не являются каскадом.

Запрещено показывать `% от прошлого`.

Колонки:

```text
Gate / Reason
Eligible bars
Pass count
Fail count
Pass rate
Source columns
Denominator rule
Notes
```

Базовые строки:

| Gate / Reason | Denominator rule | Pass rule |
|---|---|---|
| Time window | bars where `time_filter_enabled == 1`, otherwise `SKIP` | `time_filter_in_window == 1` |
| Candidate height | bars where `wakeup_regime_active == 1` and column exists | `wakeup_entry_candidate_height_ok == 1` |
| Candidate age | bars where `wakeup_regime_active == 1` and column exists | `wakeup_entry_candidate_age_ok == 1` |
| ATR | bars where `wakeup_regime_active == 1` and column exists | `wakeup_entry_atr_ok == 1` |
| Volume | bars where `wakeup_regime_active == 1` and column exists | `wakeup_entry_volume_ok == 1` |
| Trade mode | bars where `wakeup_regime_active == 1` and column exists | `wakeup_entry_trade_mode_ok == 1` |
| Filter ON | all bars with `trade_filter_state` | state != OFF or mode-specific active states |
| Wakeup all OK | bars where `wakeup_regime_active == 1` and column exists | `wakeup_entry_all_ok == 1` |
| Entry allowed | bars where `filter_allowed_entry` exists | `filter_allowed_entry == 1` |
| Trade opened | candidate event universe | entry exists on/after candidate by defined matching rule |

Если source column отсутствует:

- строка остается на листе;
- counts = `missing`;
- status/notes = `SKIP: source column missing`.

### 13.1. First blocking reason attribution

Дополнительный блок `First blocking reason attribution` разрешен только если из `fd_100` есть явный first-blocking reason или приоритет причин документирован в diagnostics layer.

Запрещено выдавать независимые flags за последовательную причинность.

## 14. Filter_Attribution

Filter_Attribution является proxy-аналитикой, не PnL-фактом.

### 14.1. Event universe

События для attribution:

- `Allowed entries`: фактические сделки из `trades_100`, event index = `entry_index`;
- `Blocked events`: бары из `fd_100`, где была попытка или кандидат на вход, но вход был заблокирован.

Blocked event входит в universe только если выполняется хотя бы одно условие:

```text
immediate_candidate_entry_used == 0 and immediate_candidate_entry_block_reason not in empty/NA
or wakeup_entry_all_ok == 0 with wakeup_regime_active == 1 and at least one wakeup entry gate column exists
or filter_allowed_entry == 0 and filter_block_reason not in empty/NA
```

Все обычные бары без candidate/attempt исключаются.

### 14.2. Категории

Категории:

```text
Allowed entries
Blocked by filter_off
Blocked by time reset
Blocked by daily reset
Blocked by ATR
Blocked by volume
Blocked by candidate
Blocked by trade mode
Blocked by candidate age
Blocked by unknown/other
```

Mapping source:

- сначала использовать explicit block reason columns;
- если их нет, использовать wakeup gate columns;
- если category не может быть определена, писать `Blocked by unknown/other`.

### 14.3. Таблица

Колонки:

```text
Category
Count
T+1 mean
T+3 mean
T+5 mean
T+10 mean
Positive %
Negative %
Notes
```

Метрики считаются через `compute_forward_returns`.

На листе обязательно примечание:

```text
This is a fixed-horizon close-to-close counterfactual proxy, not realized PnL.
```

Запрещенные названия:

```text
Saved PnL
Lost PnL
Net filter value
```

Разрешенные названия:

```text
Blocked negative forward return proxy
Blocked positive forward return proxy
Net forward-return proxy
Block directional quality
Missed opportunity proxy
```

## 15. Trade_Analytics

Trade_Analytics является единым источником per-trade v2-метрик.

### 15.1. Required input

Требуется:

```text
trades_100
df with open/high/low/close
```

Если `trades_100` пустой или отсутствует, писать пустой DataFrame с колонками контракта.

### 15.2. Колонки

Минимальные колонки:

```text
trade_id
direction
entry_index
exit_index
clipped_exit_index
is_clipped_exit
entry_price
exit_price
net_pnl_pct
gross_mfe_pct
gross_mae_pct
edge_ratio
r_multiple
time_to_mfe_bars
time_to_mae_bars
gross_giveback_pct
exit_efficiency
cycle_id
trade_idx_in_cycle
cycle_age_at_entry
is_in_cycle
regime_at_entry
entry_hour
entry_day
entry_month
entry_weekday
data_quality_status
```

### 15.3. Index clipping

Для каждой сделки:

```text
clipped_exit_index = min(max(exit_index, entry_index), len(df) - 1)
is_clipped_exit = exit_index >= len(df) or exit_index < entry_index
```

MFE/MAE считаются только на доступном диапазоне:

```text
[entry_index, clipped_exit_index]
```

Если `entry_index` вне диапазона df, метрики MFE/MAE = NaN, `data_quality_status = "invalid_entry_index"`.

### 15.4. Direction-aware формулы

Для long:

```text
gross_mfe_pct = (max(high[range]) - entry_price) / entry_price * 100
gross_mae_pct = (min(low[range]) - entry_price) / entry_price * 100
```

Для short:

```text
gross_mfe_pct = (entry_price - min(low[range])) / entry_price * 100
gross_mae_pct = (entry_price - max(high[range])) / entry_price * 100
```

`gross_mae_pct` обычно <= 0.

Derived metrics:

```text
edge_ratio = gross_mfe_pct / abs(gross_mae_pct), if gross_mae_pct != 0
r_multiple = net_pnl_pct / abs(gross_mae_pct), if gross_mae_pct != 0
gross_giveback_pct = gross_mfe_pct - max(net_pnl_pct, 0)
exit_efficiency = net_pnl_pct / gross_mfe_pct, if gross_mfe_pct != 0
```

`time_to_mfe_bars` и `time_to_mae_bars` считаются как offset от `entry_index` до бара extreme.

## 16. Cycle mapping

Добавить общий util:

```text
derive_trade_cycle_map(fd_100, trades_100) -> pd.DataFrame
```

Он используется только в:

- `Trade_Analytics`;
- `Cycle_Summary`;
- `Run_Health` coverage.

### 16.1. Совместимость с existing cycle sheet

`derive_trade_cycle_map` должен быть совместим с текущей логикой existing `cycle` sheet.

Правило реализации:

- использовать те же критерии active state/segment boundaries, что и existing cycle builder;
- если existing builder различает zigzag и volume-only mode, `derive_trade_cycle_map` тоже должен различать mode;
- старый лист `cycle` не должен изменить значения из-за добавления v2.

До изменения existing cycle helper'ов нужен parity test для старого `cycle` sheet.

### 16.2. Output columns

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

`mapping_status`:

```text
mapped
outside_cycle
missing_entry_index
invalid_entry_index
missing_fd_100
unsupported_mode
```

## 17. Cycle_Summary

Cycle_Summary строится из `cycle_map`, `fd_100`, `trades_100`.

Минимальные блоки:

1. Cycle overview:

```text
cycle_id
cycle_start_index
cycle_end_index
duration_bars
trade_count
cycle_net_pnl_pct
positive_trades_pct
max_trade_idx
mapping_status
```

2. Trade number in cycle:

```text
trade_idx_in_cycle
trade_count
mean_pnl_pct
median_pnl_pct
win_rate
notes
```

`Trade_Number_In_Cycle` не является отдельным листом.

## 18. Equity_Drawdown

Основной equity для v2:

```text
trade-equity = cumulative sum of trades_100.net_pnl_pct
```

На листе обязательно примечание:

```text
Trade-equity DD does not include intratrade mark-to-market drawdown.
```

Блоки:

1. Equity by trade:

```text
trade_id
net_pnl_pct
equity
running_max
underwater
```

2. DD episodes:

```text
episode_id
start_trade_id
bottom_trade_id
recovery_trade_id
depth_pct
duration_trades
recovery_trades
status
```

3. Worst 10 DD:

```text
rank
episode_id
depth_pct
duration_trades
recovery_trades
status
```

Если engine MaxDD считается по bar-level equity, не требовать равенства с trade-equity MaxDD. Показывать как разные определения.

## 19. Cost_Sensitivity

Cost_Sensitivity использует simplified per-trade cost stress model.

### 19.1. Units

Все stress values задаются в:

```text
bps per side
```

Tick slippage разрешен только если `run_metadata["tick_size"]` явно присутствует. Иначе tick scenarios не пишутся.

### 19.2. Formula

Для каждой сделки:

```text
round_trip_cost_pct = 2 * bps_per_side / 100
stressed_net_pnl_pct = gross_pnl_pct - round_trip_cost_pct
```

Если `gross_pnl_pct` отсутствует:

```text
stressed_net_pnl_pct = net_pnl_pct - additional_round_trip_cost_pct
```

В таком случае `cost_model_status = "proxy_from_net"`.

### 19.3. Scenarios

| Scenario | Unit |
|---|---|
| actual cost | from trades |
| commission 0 bps | bps per side |
| commission 0.5 bps | bps per side |
| commission 1 bps | bps per side |
| commission 2 bps | bps per side |
| slippage 0.5 bps | bps per side |
| slippage 1 bps | bps per side |
| commission 1 bps + slippage 1 bps | bps per side |

### 19.4. Metrics

For each scenario:

```text
scenario
unit
net_pnl_pct
avg_trade_pct
median_trade_pct
profit_factor
win_rate
max_dd_trade_equity
per_trade_sharpe
breakeven_bps_per_side
cost_model_status
```

`per_trade_sharpe` и `max_dd_trade_equity` должны быть явно подписаны как per-trade definitions.

Сверка actual-cost recomputed metrics vs `Metrics_100`:

- `PASS`, если определения совпали в tolerance;
- `WARN`, если отличаются из-за documented definition mismatch;
- `SKIP`, если не хватает колонок.

## 20. Remediation

Remediation строится из результатов Фазы A.

Колонки:

```text
Priority
Symptom
Detection metric
Observed
Threshold
Likely cause
Parameter family
Suggested action
Source sheet
Confidence
```

Базовые правила:

| Symptom | Detection metric | Likely cause | Parameter family | Suggested action |
|---|---|---|---|---|
| false-start high | false-start % > threshold | noise entries | candidate/reversal thresholds | tighten confirmation |
| giveback high | avg giveback > threshold | late exit | TTL/exit/trailing | reduce giveback |
| edge decays by trade # | trade_idx_in_cycle N+ median < 0 | overtrading | cycle trade limit | lower max trades |
| filter blocks positive proxy | blocked T+N positive high | filter too strict | ATR/volume/candidate | relax filter |
| bad hours | hour bucket PnL negative | time regime | time filter | exclude weak hours |
| cost fragile | breakeven bps too low | no edge buffer | execution/frequency | reduce churn |

Если source metric отсутствует, remediation row не должна выдавать рекомендацию как факт. Писать `SKIP`.

## 21. FilterDiagnostics_sampled

`FilterDiagnostics_sampled` входит в Фазу A.

Цель: дать lightweight-доступ к raw diagnostics без дублирования полного `FilterDiagnostics_100`.

Правила:

- лист строится из `fd_100`;
- default sample size: первые 200 строк, последние 200 строк, плюс строки вокруг сделок;
- максимальный размер по умолчанию: 2000 rows;
- sample strategy указывается на листе;
- если `fd_100` отсутствует, лист не пишется или пишется пустым по дочернему флагу с `SKIP`.

Колонки сохраняют исходные diagnostic key names без display rename, чтобы лист был удобен для отладки.

## 22. Threshold constants

Все цветовые/статусные flags берутся из единой таблицы-константы:

```text
DIAGNOSTICS_V2_THRESHOLDS
```

Минимальные поля:

```text
flag
operator
value
unit
description
```

Defaults:

| Flag | Rule | Unit |
|---|---|---|
| `pf_weak` | PF < 1.1 | ratio |
| `median_negative` | median trade < 0 | pct |
| `false_start_high` | false-start % > 30 | pct |
| `avg_trade_too_small` | avg trade <= estimated round-trip cost | pct |
| `cost_fragile` | stressed PnL loss > 50 | pct |
| `low_filter_coverage` | Filter ON % < 15 | pct |
| `dd_duration_high` | recovery duration > 50 | trades |
| `cycle_overtrade` | trade # N median PnL < 0 | pct |
| `giveback_high` | avg gross giveback > 50% of avg gross MFE | pct |

YAML override не входит в Фазу A. Это отдельное расширение.

## 23. Фаза B

Фаза B не входит в первый implementation scope, но контракты листов резервируются.

Листы:

```text
Returns_Calendar
Exit_Quality
False_Start_2
Data_Dictionary
```

### 23.1. Returns_Calendar

Агрегаты по `trades_100.entry_time`:

- month;
- day;
- hour;
- weekday.

### 23.2. Exit_Quality

Агрегаты по `exit_reason`, если колонка есть в `trades_100`.

Post-exit returns используют `compute_forward_returns` от `exit_index`.

### 23.3. False_Start_2

Не заменяет existing false-start sheet в Фазе A.

В Фазе B создается новый v2-лист:

```text
False_Start_2
```

Имя без точки, чтобы избежать неоднозначностей в tooling.

Existing false-start sheet остается без изменения.

### 23.4. Data_Dictionary

Описывает v2-листы, колонки, формулы, units и proxy disclaimers.

## 24. Фаза C

Фаза C не входит в первый implementation scope.

Лист:

```text
Robustness
```

Содержит:

- bootstrap mean trade CI;
- Monte Carlo trade order reshuffle;
- p-value/proxy significance;
- tail-risk diagnostics.

Все statistical significance KPI находятся только здесь, не в Dashboard Фазы A.

## 25. Нефункциональные требования

### 25.1. Performance budgets

Для dataset класса `~300k rows x ~80 columns`:

- export time overhead Фазы A: не более `+30%` к baseline exporter time;
- output file size overhead без full raw diagnostics: не более `+25%`;
- additional peak memory: не более `+300 MB`;
- `FilterDiagnostics_sampled`: не более `2000` rows по умолчанию.

Если budget не может быть измерен в unit-тестах, нужен benchmark/smoke script и зафиксированный результат.

### 25.2. Memory

Запрещено без необходимости делать полные копии `fd_100` как DataFrame размера `len(df) x all_keys`.

Разрешено:

- работать по numpy arrays;
- строить агрегаты напрямую;
- материализовать sampled diagnostics;
- материализовать только нужные колонки для конкретного листа.

### 25.3. Excel row limits

Любой лист, который может приблизиться к Excel row limit, должен иметь guard.

Для v2 Фазы A raw/full per-bar листы запрещены, кроме sampled.

## 26. Build/write contracts

Для каждого v2-листа нужны функции:

```text
_empty_<sheet>_df(...) -> pd.DataFrame
_build_<sheet>_df(ctx: DiagnosticsV2Context) -> pd.DataFrame
_write_<sheet>_sheet(writer: pd.ExcelWriter, df: pd.DataFrame) -> None
```

Допускается общий writer helper для простых листов, но build-функция и empty-функция должны быть отдельными и unit-testable.

Каждая build-функция обязана:

- возвращать DataFrame с фиксированным порядком колонок;
- корректно работать на empty/missing inputs;
- не писать в Excel;
- не мутировать `ctx` inputs;
- не запускать engine/backtest;
- не читать filesystem.

## 27. Acceptance Criteria Фазы A

Фаза A считается готовой, если:

1. При `export_diagnostics_v2=False` workbook полностью сохраняет legacy behavior.
2. При filter disabled v2-листы не пишутся.
3. При filter enabled + `export_diagnostics=True` + `export_diagnostics_v2=True` пишутся включенные v2-листы Фазы A.
4. Старые `Metrics_*` и `Trades_*` листы остаются.
5. Existing `cycle` sheet остается совместимым с текущими тестами.
6. `Cycle_Summary` и `Trade_Analytics` используют один `derive_trade_cycle_map`.
7. `Filter_Attribution` явно маркирует proxy-метрики.
8. `Run_Health` не FAIL'ит warmup по правилу `entry_index >= warmup`.
9. `Cost_Sensitivity` явно маркирует simplified per-trade cost model.
10. Все v2 build-функции покрыты unit-тестами на normal/empty/missing data.
11. Disabled-path golden/parity tests проходят.
12. Performance budget проверен smoke benchmark'ом или документированным измерением.

## 28. Минимальный тестовый набор

Unit tests:

- `_build_reproducibility_df`;
- `_build_run_health_df`;
- `compute_forward_returns`;
- `_build_filter_funnel_df`;
- `_build_filter_attribution_df`;
- `_build_trade_analytics_df`;
- `derive_trade_cycle_map`;
- `_build_cycle_summary_df`;
- `_build_equity_drawdown_df`;
- `_build_cost_sensitivity_df`;
- `_build_remediation_df`;
- `_build_filter_diagnostics_sampled_df`.

Integration tests:

- disabled filter path: no v2 sheets;
- `export_diagnostics_v2=False`: no v2 sheets;
- enabled v2 path: v2 sheet presence and order;
- child flags disable individual sheets;
- legacy multi-period sheets still present;
- old cycle sheet unchanged;
- v2 sheets do not require changes in engine modules.

Golden/parity tests:

- disabled-path workbook structure remains bit-identical;
- existing cycle sheet header/order remains unchanged;
- existing false-start sheet remains unchanged in Фазе A.

## 29. Out of scope

Не входит в это ТЗ:

- изменение trading engine;
- изменение `signal_events.py`;
- добавление T+5/T+10 в signal events;
- изменение логики фильтра;
- изменение расчета сделок;
- v2 rollout в WF exporter;
- v2 rollout в alternate donor zigzag exporter;
- YAML override для thresholds;
- статистическая значимость Фазы C;
- полный code-level lookahead audit.

## 30. Итоговый контракт

Итоговая формула реализации:

```text
canonical tester exporter
-> disabled path bit-identical
-> old sheets unchanged
-> v2 gated by explicit export_diagnostics_v2
-> pr_100-only analytics
-> no engine changes
-> one DiagnosticsV2Context
-> verifiable Run_Health only
-> Trade_Analytics from df/trades
-> Filter_Attribution as fixed-horizon proxy
-> one cycle mapping util
-> Cost_Sensitivity in bps with simplified per-trade model
-> Phase A first
-> B/C reserved but disabled by default
```

