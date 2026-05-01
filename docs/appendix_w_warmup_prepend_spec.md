# Приложение W. Спецификация warmup и prepend-семантики

**Appendix W — Warmup / Prepend Execution Spec**

Приложение к плану реализации WF Grid Search, Phase A.
Версия: 1.0
Дата: 2026-03-31

---

## W.1. Назначение приложения

Данный документ:

1. Фиксирует canonical warmup/prepend-семантику для grid-проекта.
2. Отделяет OOS prepend path от legacy fallback path.
3. Определяет authoritative слой данных после trim.
4. Фиксирует инварианты, edge cases и failure modes.
5. Является самодостаточным spec-контрактом: по нему можно реализовать warmup/prepend-логику в `wf/step_executor.py` без чтения основного плана реализации.

Документ **не описывает**: общую архитектуру grid, ranking, aggregation, export, TOP-K, consensus, mini-grid, refine. Только warmup и prepend execution semantics.

---

## W.2. Термины и определения

| Термин | Определение |
|---|---|
| `warmup_period` | Число баров, запрошенное для разогрева индикатора. Передаётся в `run_single_backtest` как параметр. Внутри `calculate_all_metrics` первые `warmup_period` баров returns/equity исключаются из расчёта ratio-метрик (sharpe, sortino, cagr, max_drawdown). Trade-метрики (num_trades, win_rate, sum_pnl_pct, profit_factor, avg_trade) считаются на полной истории **без** warmup-среза. |
| `effective_warmup` | Фактический warmup, применённый после safety-cap внутри `calculate_all_metrics`. Формула: `effective_warmup = min(warmup_period, max(0, len(returns) - 2))`. Гарантирует, что после warmup остаётся минимум 2 бара для ratio-метрик. Хранится в `BacktestResult.effective_warmup`. |
| `prepend_bars_requested` | Результат вызова `calculate_warmup(len(full_dataset), config)` — число баров, запрошенное для prepend-прогрева индикатора перед OOS-окном. Вычисляется один раз на pipeline, не на шаг. |
| `prepend_bars_applied` | Фактически применённый prepend: `min(prepend_bars_requested, test_start_idx)`. Ограничен доступной историей перед OOS-окном. |
| `oos_boundary` | Индекс в extended-массивах, разделяющий prepend-зону и OOS-зону. Численно равен `prepend_bars_applied`. Все массивы делятся на `[0 : oos_boundary)` (prepend) и `[oos_boundary : end]` (OOS-only). |
| `extended slice` | Срез данных `full_data[ext_start_idx : test_end_idx]`, где `ext_start_idx = test_start_idx - prepend_bars_applied`. Включает prepend-бары + OOS-бары. Подаётся на вход `run_single_backtest`. |
| `OOS-only slice` | Массивы после trim: `ext_arrays[oos_boundary:]`. Содержат только OOS-данные. Являются единственным authoritative источником для downstream (step_oos_long, aggregation, export). |
| `legacy OOS path` | Резервный путь, при котором backtest выполняется на чистом OOS-slice `data[test_start_idx : test_end_idx]` без prepend. Метрики берутся из `BacktestResult.metrics` напрямую. |

---

## W.3. Источники donor-семантики

### W.3.1. Донорские файлы

Целевая prepend-семантика извлечена из двух файлов donor-пакета `supertrend_optimizer`:

| Файл | Роль |
|---|---|
| `walk_forward.py` | Содержит три реализации canonical prepend path: в `_fill_oos_metrics_for_topk` (строки 2120–2243), в `_fill_ref_oos_metrics` (строки 3083–3159), в main OOS path (строки 3783–3878). Алгоритм идентичен во всех трёх. |
| `test_oos_prepend.py` | Содержит четыре группы инвариантов (A–D), формализующих требования к prepend path: exact OOS window length, unchanged WF boundaries, array alignment after trim, metrics on trimmed OOS only. |

### W.3.2. Границы заимствования

Из donor-файлов берётся **только**:

- Prepend execution algorithm (compute → extend → backtest → trim → recompute → filter trades).
- Boundary / trim semantics.
- OOS-only metrics recomputation contract.
- Invariants A–D.

**Не переносится** в grid: TOP-K selection, consensus logic, mini-grid / refine, robustness / diversification filter, `WalkForwardResult` / `WFStepResult` dataclasses, optimization per step.

---

## W.4. Canonical OOS prepend path

### W.4.1. Условие активации

Canonical prepend path активен, когда executor получает:

- Full OHLC arrays (open, high, low, close) — полный dataset, не подрезанный под окно.
- Full index — полный индекс dataset.
- Absolute bounds (`test_start_idx`, `test_end_idx`) из `WFWindowSlice`.

Это соответствует donor-логике `_use_prepend` (`walk_forward.py`, строки 2110–2118): проверяется наличие full arrays + boundary indices, без проверки `prepend_bars > 0`.

Когда `prepend_bars_applied == 0` (например, `test_start_idx == 0` на первом шаге), canonical path **по-прежнему активен**: extended slice совпадает с чистым OOS-slice, trim не отрезает ничего, `calculate_all_metrics(..., warmup_period=0)` вычисляет метрики на полном OOS-окне. Это обеспечивает единый data flow через один путь кода.

### W.4.2. Алгоритм — пошагово

**Шаг 1. Compute `prepend_bars` (один раз на pipeline)**

```
prepend_bars_requested = calculate_warmup(len(full_dataset), config)
```

Функция `calculate_warmup` из `utils/warmup.py`:
- `base = max(config.validation.warmup_period, atr_period_max)`
- Если `warmup_period_auto`: `auto = clamp(int(n * 0.10), 100, 400)`, `auto = max(auto, atr_period_max)`, `warmup = max(base, auto)`
- Иначе: `warmup = base`

Константы: `DEFAULT_WARMUP_FRACTION = 0.10`, `MIN_AUTO_WARMUP = 100`, `MAX_AUTO_WARMUP = 400`.

`prepend_bars_requested` не зависит от конкретной grid point (не зависит от `atr_period` / `multiplier`). Вычисляется один раз.

**Шаг 2. Apply per-step clamp**

```
prepend_bars_applied = min(prepend_bars_requested, wf_slice.test_start_idx)
```

Ограничивает prepend доступной историей перед OOS-окном. На первом WF-шаге `test_start_idx` может быть меньше `prepend_bars_requested` → prepend будет частичным. Это нормальное поведение, не fallback.

**Шаг 3. Form extended slice**

```
ext_start_idx = wf_slice.test_start_idx - prepend_bars_applied
```

Данные для backtest:
- `ext_open  = full_open[ext_start_idx : test_end_idx]`
- `ext_high  = full_high[ext_start_idx : test_end_idx]`
- `ext_low   = full_low[ext_start_idx : test_end_idx]`
- `ext_close = full_close[ext_start_idx : test_end_idx]`
- `ext_index = full_index[ext_start_idx : test_end_idx]`

Длина extended slice: `test_end_idx - ext_start_idx = (test_end_idx - test_start_idx) + prepend_bars_applied`.

**Шаг 4. Run backtest on extended slice**

```
ext_result = run_single_backtest(
    open_prices=ext_open,
    high=ext_high,
    low=ext_low,
    close=ext_close,
    index=ext_index,
    atr_period=grid_point.atr_period,
    multiplier=grid_point.multiplier,
    trade_mode=grid_point.trade_mode,
    commission=config.backtest.commission,
    warmup_period=config.validation.warmup_period,
    early_exit_enabled=oos_early_exit,
    early_exit_max_drawdown=...,
    early_exit_check_bars=...,
    periods_per_year=periods_per_year,
    min_trades_required=config.backtest.min_trades_required,
    extract_trades_flag=True,
    auto_warmup=True,
    execution_model=ExecutionModel.OPEN_TO_OPEN,
)
```

Grid всегда вызывает с `extract_trades_flag=True` (необходимы trades для `WF_Trades` листа). Это отличие от donor TOP-K / reference paths, где `extract_trades_flag=False`.

**Шаг 5. Extract extended arrays**

```
ext_returns   = ext_result.returns         # длина: len(ext_slice) - 1
ext_equity    = ext_result.equity_curve    # длина: len(ext_slice)
ext_positions = ext_result.positions       # длина: len(ext_slice)
ext_trades_df = ext_result.trades_df       # trades на полном extended slice
```

Инвариант donor: `len(equity_curve) == len(returns) + 1`, `len(positions) == len(returns) + 1`. Проверяется в `BacktestResult.__post_init__`.

При early_exit массивы могут быть укорочены относительно extended slice.

Guard: если любой массив `None` → defensive fallback (§W.5.2).

**Шаг 6. Determine OOS boundary + guard**

```
oos_boundary = prepend_bars_applied
```

Guard: если `oos_boundary >= len(ext_returns)` → defensive fallback (§W.5.2).

Это может произойти если early_exit обрезал массивы до длины меньшей, чем prepend. В штатном режиме (без early_exit) `oos_boundary < len(ext_returns)` гарантировано, потому что `len(ext_returns) = len(ext_slice) - 1 = (oos_window + prepend) - 1`, а `oos_boundary = prepend`, значит `oos_boundary < len(ext_returns)` когда `oos_window >= 2`.

**Шаг 7. Trim arrays to OOS-only**

```
oos_returns   = ext_returns[oos_boundary:]
oos_equity    = ext_equity[oos_boundary:]
oos_positions = ext_positions[oos_boundary:]
```

Ожидаемые длины после trim:
- `len(oos_returns) == (test_end_idx - test_start_idx) - 1` (N баров OOS → N−1 returns)
- `len(oos_equity) == len(oos_returns) + 1`
- `len(oos_positions) == len(oos_returns) + 1`

При early_exit на extended slice длины будут меньше ожидаемых. Это фиксируется в `step_result.early_exit`.

**Шаг 8. Recompute OOS ratio metrics**

```
oos_bar_metrics = calculate_all_metrics(
    returns=oos_returns,
    equity_curve=oos_equity,
    positions=oos_positions,
    warmup_period=0,
    periods_per_year=periods_per_year,
    min_trades_required=config.backtest.min_trades_required,
)
```

`warmup_period=0` — каноническое значение. Индикатор уже прогрет prepend-барами; дополнительный warmup для ratio-метрик OOS-slice не нужен. Safety-cap в `calculate_all_metrics` при `warmup_period=0` не срабатывает: `max_allowed_warmup = max(0, len(returns) - 2)`, `0 <= max_allowed_warmup` всегда.

`oos_bar_metrics` содержит все стандартные ключи: `sharpe`, `sortino`, `sum_pnl_pct`, `max_drawdown`, `cagr`, `win_rate`, `num_trades`, `profit_factor`, `avg_trade`, `net_pnl_pct`, `effective_warmup`. Все значения вычислены на OOS-only массивах.

**Шаг 9. Filter trades to OOS-only**

Из `ext_trades_df` (trades на extended slice) отфильтровать только OOS-trades:

```
oos_trades_df = ext_trades_df[ext_trades_df["entry_index"] >= oos_boundary].copy()
oos_trades_df["entry_index"] -= oos_boundary
oos_trades_df["exit_index"]  -= oos_boundary
```

Семантика фильтра: trade включается в OOS-слой только если она **открылась** в OOS-зоне (`entry_index >= oos_boundary`). Trades, открывшиеся в prepend-зоне (даже если завершились в OOS), отбрасываются — их PnL частично относится к prepend-периоду.

Rebase: `entry_index` и `exit_index` пересчитываются в координаты OOS-окна (0-based от начала OOS). Это необходимо для консистентности с trades из legacy path, где индексы уже 0-based.

**Шаг 10. Trade-level override после trim (grid-specific contract)**

Это **grid-специфичный шаг, отсутствующий в доноре**. Обоснование — в §W.4.3.

Из отфильтрованных `oos_trades_df` пересчитать trade-level метрики и перезаписать соответствующие ключи в `oos_bar_metrics`:

При `len(oos_trades_df) > 0`:
- `num_trades` ← `len(oos_trades_df)`
- `sum_pnl_pct` ← `oos_trades_df["net_pnl_pct"].sum()`
- `win_rate` ← `(count(net_pnl_pct > 0) / num_trades) * 100.0`
- `profit_factor` ← `sum(positive_pnl) / abs(sum(negative_pnl))`; при нуле losses → `MAX_VALID_METRIC` (9999.0); при нуле profits → `0.0`; при нуле обоих → `INVALID_METRIC_VALUE` (−999.0)
- `avg_trade` ← `sum_pnl_pct / num_trades`
- `net_pnl_pct` ← `avg_trade` (alias)

При `len(oos_trades_df) == 0`:
- `num_trades` ← `0`
- `sum_pnl_pct` ← `0.0`
- `win_rate` ← `0.0`
- `avg_trade` ← `INVALID_METRIC_VALUE`
- `profit_factor` ← `INVALID_METRIC_VALUE`
- `net_pnl_pct` ← `INVALID_METRIC_VALUE`
- `sharpe` ← `INVALID_METRIC_VALUE`
- `sortino` ← `INVALID_METRIC_VALUE`
- `cagr` ← `INVALID_METRIC_VALUE`

При `num_trades < min_trades_required` и `num_trades > 0`:
- `sharpe` ← `INVALID_METRIC_VALUE`
- `sortino` ← `INVALID_METRIC_VALUE`
- `cagr` ← `INVALID_METRIC_VALUE`
- `max_drawdown` — **не инвалидируется** (equity-based, валидна при любом числе trades)

Ratio-метрики (`sharpe`, `sortino`, `cagr`, `max_drawdown`) за пределами перечисленных guards **не перезаписываются** trade-level данными — остаются bar-level из шага 8. Это соответствует контракту `run_single_backtest` (`run.py`, строки 335–337: ratio metrics kept from Step 3).

**Шаг 11. Assemble OOS-only step result**

Финальный `StepResult` содержит:
- `metrics` → финальные метрики после шага 10
- `oos_trades_df` → отфильтрованные и rebase-нутые trades
- `oos_returns`, `oos_equity`, `oos_positions` → OOS-only массивы (опционально, для диагностики)
- Диагностические поля (§W.8)
- `early_exit` → из `ext_result.early_exit`

### W.4.3. Обоснование шага 10 (trade-level override)

Donor prepend path (`_fill_oos_metrics_for_topk`, reference backtest) использует `extract_trades_flag=False` — trades не извлекаются, trade-level override не нужен. `oos_bar_metrics` из `calculate_all_metrics` являются финальными.

В grid `extract_trades_flag=True` всегда. Без шага 10 возникает семантическое расхождение:

| Path | `sum_pnl_pct` | `num_trades` |
|---|---|---|
| Legacy (no prepend) | trade-level: `sum(trades_df["net_pnl_pct"])` — из встроенного override в `run_single_backtest` | trade-level: `len(trades_df)` |
| Canonical prepend **без шага 10** | bar-level: `sum(returns) * 100` на trimmed OOS | bar-level: из `positions` на trimmed OOS |

Bar-level и trade-level `sum_pnl_pct` — разные числа (compounding effect, pending trades, commission distribution). При смешивании шагов с разной семантикой агрегация строится на несопоставимых величинах → ranking некорректен.

Шаг 10 гарантирует: после его выполнения финальный `step_result.metrics` имеет ту же семантику, что `BacktestResult.metrics` после `run_single_backtest(extract_trades_flag=True)` — trade-level для trade-метрик, bar-level для ratio-метрик. Canonical path и legacy path дают **сопоставимые** значения.

Протокол шага 10 повторяет протокол Step 4.5 в `run.py` (строки 279–363) — та же последовательность override, те же guards, те же sentinel/cap значения. Это не новый экспериментальный алгоритм, а перенос donor-контракта в grid-контекст.

---

## W.5. Fallback paths

### W.5.1. Legacy fallback

**Trigger**: full OHLC arrays, full index или absolute bounds (`test_start_idx`, `test_end_idx`) **не переданы** в executor. Технически невозможно построить extended slice.

**Поведение**:
- `run_single_backtest` выполняется на чистом OOS-slice `data[test_start_idx : test_end_idx]`
- Параметры: `extract_trades_flag=True`, `auto_warmup=True`
- Метрики берутся из `BacktestResult.metrics` (включая встроенный trade-level override из Step 4.5 в `run.py`)
- Trades берутся из `BacktestResult.trades_df` без фильтрации (индексы уже 0-based)
- Warmup применяется внутри `calculate_all_metrics` к ratio-метрикам — это означает, что первые `effective_warmup` баров OOS-окна исключаются из Sharpe/Sortino/CAGR/max_drawdown

**Диагностика**: `used_prepend = False`, `used_legacy_oos_path = True`.

Legacy path — это **fallback**, а не штатный путь. В рабочей конфигурации grid executor всегда получает full arrays + bounds.

### W.5.2. Defensive fallback

**Trigger**: canonical prepend path активирован, но после `run_single_backtest` на extended slice:
- Raw arrays (`returns`, `equity_curve`, `positions`) возвращены как `None`, **или**
- `oos_boundary >= len(ext_returns)` (early_exit обрезал массивы до длины меньше prepend)

**Поведение**:
- Метрики берутся из `ext_result.metrics` (включая trade-level override, если `extract_trades_flag=True`)
- Trades берутся из `ext_result.trades_df` без фильтрации
- Warning в лог с указанием `oos_boundary`, `len(ext_returns)`, grid_point_id, wf_step

**Диагностика**: `used_prepend = False`, `used_legacy_oos_path = False` (отдельный defensive path). Executor может ввести отдельный флаг `used_defensive_fallback = True`.

**Замечание**: в defensive fallback метрики включают prepend-бары (если `run_single_backtest` отработал на extended slice). Это менее точно, чем canonical path, но безопаснее, чем crash.

---

## W.6. Train path semantics

Train backtest **не использует prepend**. Фиксируется как отдельный контракт.

| Аспект | Спецификация |
|---|---|
| Input | `data[train_start_idx : train_end_idx]` — чистый train-slice из `WFWindowSlice` |
| Prepend | Не применяется |
| Warmup | Передаётся `warmup_period` из config; `auto_warmup=True`. Warmup «съедает» начало тренировочного окна для ratio-метрик. |
| Обоснование | Train-окно обычно в 3–5 раз длиннее OOS → warmup отрезает малую долю. Донор не применяет prepend к train (`walk_forward.py`, строки 3911–3932). |
| extract_trades_flag | `True` в grid (нужны train trades для `WF_Train_Trades` листа) |
| Результат | `BacktestResult.metrics` и `BacktestResult.trades_df` используются напрямую |
| Диагностика | `warmup_used`, `warmup_effective` (из `BacktestResult.warmup`, `.effective_warmup`) |

Prepend-контракт (§W.4) относится **только** к OOS path.

---

## W.7. Контракт слоёв данных

### W.7.1. Extended result (внутренний)

| Свойство | Значение |
|---|---|
| Область видимости | Только внутри `wf/step_executor.py` |
| Содержание | Полный `BacktestResult` от `run_single_backtest` на extended slice: prepend + OOS данные |
| Массивы | `ext_returns`, `ext_equity`, `ext_positions` — содержат prepend-бары |
| Метрики | `ext_result.metrics` — вычислены на extended slice с warmup внутри `run_single_backtest`; **не авторитетны** для OOS |
| Trades | `ext_result.trades_df` — содержат trades из prepend-зоны; **не авторитетны** для OOS |
| Экспорт | **Никуда не попадает**: ни в `step_oos_long`, ни в `candidate_aggregate`, ни в `summary_wide`, ни в XLSX |

### W.7.2. OOS-only result (authoritative)

| Свойство | Значение |
|---|---|
| Область видимости | Единственный внешний слой для всех downstream-потребителей |
| Содержание | `StepResult` после trim + trade-level override (шаги 7–11 из §W.4.2) |
| Массивы | `oos_returns`, `oos_equity`, `oos_positions` — только OOS-бары |
| Метрики | `step_result.metrics` — ratio из bar-level на OOS-only, trade-метрики из OOS-only trades |
| Trades | `step_result.oos_trades_df` — только trades, открывшиеся в OOS-зоне, с rebase-нутыми индексами |
| Потребители | `collect/step_collector.py`, `collect/trades_collector.py`, `aggregate/*`, `gates/*`, `ranking/*`, `export/*` |

### W.7.3. Правило изоляции

Extended result **не передаётся** за пределы executor. Все downstream-компоненты работают исключительно с OOS-only step result. Это гарантирует: prepend-бары не протекают в метрики, trades, aggregation или XLSX ни при каком сценарии.

---

## W.8. Обязательные диагностические поля

Каждый `StepResult` (OOS) содержит следующие диагностические поля, записываемые в `step_oos_long`:

| Поле | Тип | Источник | Описание |
|---|---|---|---|
| `prepend_bars_requested` | `int` | `calculate_warmup(len(full_dataset), config)` | Полный запрос prepend. Один для всех grid points и шагов. |
| `prepend_bars_applied` | `int` | `min(prepend_bars_requested, test_start_idx)` | Фактический prepend для данного WF-шага. Может быть < requested, если test_start_idx мал. |
| `used_prepend` | `bool` | Canonical path: `True`; fallback paths: `False` | Был ли использован canonical prepend path. |
| `used_legacy_oos_path` | `bool` | Legacy fallback: `True`; canonical + defensive: `False` | Был ли использован legacy fallback (full arrays недоступны). |
| `oos_boundary_index` | `int` | `prepend_bars_applied` при canonical path; `0` при legacy | Индекс границы в extended arrays. |
| `warmup_used` | `int` | `warmup_period`, переданный в `run_single_backtest` для данного шага | Для canonical path: из config. Для recompute (шаг 8): всегда `0`. |
| `warmup_effective` | `int` | Canonical path: `0` (warmup не нужен после prepend). Legacy path: `ext_result.effective_warmup`. | Фактический warmup, применённый к ratio-метрикам OOS. |
| `effective_oos_bars` | `int` | `len(oos_returns)` | Число баров, по которым вычислены ratio-метрики. Для canonical path: `test_window - 1` (штатно); может быть меньше при early_exit. |

---

## W.9. Инварианты

### W.9.1. Инвариант A — Exact OOS window length

После trim (canonical prepend path, без early_exit):
- `len(oos_returns) == test_end_idx - test_start_idx - 1`
- `len(oos_equity) == test_end_idx - test_start_idx`
- `len(oos_positions) == test_end_idx - test_start_idx`

Формулировка: N баров OOS → N−1 bar-returns, N equity points, N position points.

Источник: `test_oos_prepend.py`, Invariant A.

### W.9.2. Инвариант B — WF boundaries unchanged

Prepend расширяет входные данные для backtest, но **не сдвигает** WF boundaries:
- `step_result.test_start_idx == wf_slice.test_start_idx`
- `step_result.test_end_idx == wf_slice.test_end_idx`

Backtest input (extended slice) длиннее чистого OOS-окна на `prepend_bars_applied`, но границы WF-шага неизменны.

Источник: `test_oos_prepend.py`, Invariant B.

### W.9.3. Инвариант C — Array alignment after trim

После trim три массива согласованы:
- `len(oos_equity) == len(oos_returns) + 1`
- `len(oos_positions) == len(oos_returns) + 1`

Это стандартный инвариант `BacktestResult`, перенесённый на trimmed массивы.

Источник: `test_oos_prepend.py`, Invariant C.

### W.9.4. Инвариант D — No prepend leakage into metrics

`calculate_all_metrics` (шаг 8) получает:
- `returns` длины `test_window - 1`, не `prepend + test_window - 1`
- `warmup_period = 0`

Prepend-бары не попадают в ratio-метрики OOS. Все метрики вычислены исключительно по OOS-данным.

Источник: `test_oos_prepend.py`, Invariant D.

### W.9.5. Инвариант E — No prepend leakage into trades (grid OOS layer)

В `step_result.oos_trades_df` после фильтрации и rebase:
- Все `entry_index >= 0` — железное требование; ни одна trade не начинается в prepend-зоне.
- `exit_index` выражен в координатах OOS-окна (после rebase `entry_index -= oos_boundary`, `exit_index -= oos_boundary`).

Этот инвариант зафиксирован для **grid OOS layer after filtering/rebasing**. Donor equal-blocks semantics в отдельных сценариях допускает выход сделки за пределы сегмента — grid OOS layer фиксирует собственный, более строгий контракт.

Источник: `test_oos_prepend.py`, Invariant A (trade bounds).

### W.9.6. Инвариант F — Extended input longer than OOS

Если `prepend_bars_applied > 0`:
- Длина input в `run_single_backtest` > `test_end_idx - test_start_idx`

Если `prepend_bars_applied == 0`:
- Длина input == `test_end_idx - test_start_idx`

Источник: `test_oos_prepend.py`, Invariant B (extended calls).

### W.9.7. Инвариант G — Trade-level metrics authoritative after trim

После шага 10 trade-метрики (`num_trades`, `sum_pnl_pct`, `win_rate`, `profit_factor`, `avg_trade`) в `step_result.metrics` вычислены из `oos_trades_df` и являются авторитетными. Предварительные bar-level оценки из шага 8 перезаписаны. Ratio-метрики (`sharpe`, `sortino`, `cagr`, `max_drawdown`) остаются bar-level из шага 8 (единственный доступный путь).

### W.9.8. Инвариант H — Prepend consistency across grid points

`prepend_bars_requested` — один и тот же для всех grid points (не зависит от `atr_period` / `multiplier`). `prepend_bars_applied` — один и тот же для всех grid points **на одном WF-шаге** (зависит только от `test_start_idx`, который общий).

---

## W.10. Edge cases / failure modes

### W.10.1. `prepend_bars_applied = 0`

**Когда**: `test_start_idx = 0` (первый WF-шаг, OOS начинается с начала dataset) или `prepend_bars_requested = 0` (warmup не настроен).

**Поведение**: canonical path активен. Extended slice совпадает с OOS-slice. `oos_boundary = 0`, trim не отрезает ничего. `calculate_all_metrics(..., warmup_period=0)` вычисляет метрики на полном OOS-окне. Trade filter `entry_index >= 0` оставляет все trades. Поведение эквивалентно legacy path, но идёт через canonical code path.

**Это не fallback.**

### W.10.2. `test_start_idx = 0`

Частный случай §W.10.1. `prepend_bars_applied = min(requested, 0) = 0`. Canonical path работает штатно.

### W.10.3. `oos_boundary >= len(ext_returns)`

**Когда**: early_exit обрезал extended arrays до длины <= `prepend_bars_applied`.

**Поведение**: defensive fallback (§W.5.2). Метрики из `ext_result.metrics`, trades из `ext_result.trades_df`. Warning в лог.

**Причина**: невозможно вычленить OOS-only данные из массивов, если весь backtest завершился в пределах prepend-зоны.

### W.10.4. Raw arrays отсутствуют (None)

**Когда**: `ext_result.returns`, `.equity_curve` или `.positions` — `None`. Маловероятно в production (donor всегда возвращает массивы), но возможно в тестах с mock.

**Поведение**: defensive fallback (§W.5.2).

### W.10.5. Early exit на extended slice

**Когда**: `ext_result.early_exit = True`.

**Поведение**: зависит от позиции early exit.
- Если exit произошёл в prepend-зоне (`exit_bar < oos_boundary`): `oos_boundary >= len(ext_returns)` → defensive fallback (§W.10.3).
- Если exit произошёл в OOS-зоне: trim штатный, но `len(oos_returns) < expected`. `effective_oos_bars` отражает фактическую длину. Инвариант A (exact OOS window length) **не выполняется** при early exit — это ожидаемое поведение.

`step_result.early_exit = True` фиксируется для downstream.

### W.10.6. OOS trades отсутствуют после фильтрации

**Когда**: все trades на extended slice открылись в prepend-зоне (все `entry_index < oos_boundary`). После фильтра `oos_trades_df` пуст.

**Поведение**: шаг 10 обрабатывает `len(oos_trades_df) == 0` — применяет протокол empty trades (см. §W.4.2 шаг 10). `num_trades = 0`, ratio-метрики инвалидируются.

### W.10.7. Effective bars after trim слишком малы

**Когда**: `len(oos_returns)` < порога минимально осмысленного числа баров (например, < 30) — метрики статистически ненадёжны.

**Поведение**: canonical path завершается штатно; `oos_bar_metrics` формально вычислены. Контроль осмысленности — на уровне step-level diagnostics: `effective_oos_bars` записывается в `step_oos_long`. Downstream (gates или status model) может ввести gate `effective_oos_bars >= min_meaningful_bars` → `step_status = insufficient_bars`.

Данный spec **не определяет** конкретный порог `min_meaningful_bars` — это решение основного плана (status model, gates). Spec фиксирует диагностическое поле `effective_oos_bars`, по которому downstream может принять решение.

---

## W.11. Status / diagnostics consequences

### W.11.1. Влияние prepend на step status

| Ситуация | Диагностические поля | Рекомендуемый step_status |
|---|---|---|
| Canonical path, штатный trim, trades есть | `used_prepend=True`, `effective_oos_bars` = ожидаемый | `ok` |
| Canonical path, `prepend_bars_applied=0` | `used_prepend=True`, `oos_boundary_index=0` | `ok` |
| Legacy fallback | `used_legacy_oos_path=True` | `ok` (метрики из `BacktestResult.metrics`) |
| Defensive fallback | `used_prepend=False`, warning в лог | `ok` или `runtime_error` (зависит от причины) |
| OOS trades пусты после фильтрации | `num_trades=0` в метриках | Определяется status model основного плана |
| `effective_oos_bars` < `min_meaningful_bars` | Поле `effective_oos_bars` < порога | Определяется gates основного плана |
| Early exit в OOS-зоне | `early_exit=True`, `effective_oos_bars` < expected | `ok` (early exit — легитимный результат) |
| Early exit в prepend-зоне | Defensive fallback triggered | `runtime_error` или `skipped` |

### W.11.2. Рекомендация для основного плана

Основной план (status model, §3) может использовать `effective_oos_bars` для введения статуса `insufficient_bars` — когда бэктест отработал, но метрики вычислены по слишком малому числу баров для статистической значимости. Порог — конфигурируемый (например, 30).

Основной план (status model) может использовать `num_trades == 0` в финальных метриках для введения статуса `no_trades` — когда бэктест отработал, trades не было.

Данный spec **предоставляет** диагностические поля для этих решений, но **не навязывает** конкретные статусы.

---

## W.12. Требования к тестам

### W.12.1. Unit-тесты prepend path

| Тест | Описание | Инвариант |
|---|---|---|
| `test_extended_input_longer_than_oos` | Mock `run_single_backtest`. Проверить: длина input = `oos_window + prepend_bars_applied`. | F |
| `test_exact_oos_trim_length` | После trim: `len(oos_returns) == test_end_idx - test_start_idx - 1`. | A |
| `test_array_alignment_after_trim` | `len(oos_equity) == len(oos_returns) + 1`, `len(oos_positions) == len(oos_returns) + 1`. | C |
| `test_wf_boundaries_unchanged` | `step_result.test_start_idx` и `test_end_idx` не сдвинуты prepend. | B |
| `test_metrics_warmup_zero_on_trimmed` | `calculate_all_metrics` вызывается с `warmup_period=0` на массиве длины `test_window - 1`. | D |
| `test_no_prepend_trades_in_oos_layer` | Все `entry_index >= 0` в `oos_trades_df`. Нет trades из prepend-зоны. | E |
| `test_trade_indices_rebased_to_oos` | `entry_index` и `exit_index` в `oos_trades_df` выражены в координатах OOS-окна. | E |
| `test_trade_level_override_after_trim` | `num_trades`, `sum_pnl_pct` в финальных метриках соответствуют trade-level из `oos_trades_df`, а не bar-level из `calculate_all_metrics`. | G |
| `test_trade_level_override_empty_trades` | При пустом `oos_trades_df`: `num_trades=0`, `sum_pnl_pct=0.0`, ratio-метрики инвалидированы. | G |
| `test_prepend_zero_canonical_not_fallback` | При `test_start_idx=0`: canonical path активен, `used_prepend=True`, `prepend_bars_applied=0`. | — |

### W.12.2. Unit-тесты fallback paths

| Тест | Описание |
|---|---|
| `test_legacy_fallback_no_full_arrays` | Executor без full arrays → legacy path, `used_legacy_oos_path=True`. |
| `test_defensive_fallback_boundary_exceeds` | `oos_boundary >= len(ext_returns)` → defensive fallback, warning в лог. |
| `test_defensive_fallback_none_arrays` | Raw arrays = `None` → defensive fallback. |

### W.12.3. Интеграционные тесты

| Тест | Описание |
|---|---|
| `test_prepend_consistency_across_grid_points` | Два grid point на одном WF-шаге: `prepend_bars_applied` одинаков. | H |
| `test_prepend_improves_ratio_metrics` | Canonical path vs legacy path на тех же данных: canonical path не теряет OOS-бары на warmup → `effective_oos_bars` canonical >= `effective_oos_bars` legacy. |
| `test_prepend_and_legacy_same_trade_semantics` | `sum_pnl_pct` из canonical path (шаг 10) и legacy path — оба trade-level. Semantic parity. |

### W.12.4. Regression / golden тесты

| Тест | Описание |
|---|---|
| `test_golden_prepend_metrics` | Фиксированный dataset + config → prepend OOS metrics зафиксированы в snapshot. При изменении prepend-логики тест ломается. |
| `test_golden_prepend_trades_count` | Фиксированный dataset → число OOS trades после фильтрации зафиксировано в snapshot. |

---

## W.13. Что не входит в приложение

Данный spec **не описывает**:

| Тема | Где описана |
|---|---|
| TOP-K selection, consensus | Не входит в grid; donor-специфика |
| Mini-grid, refine, plateau | Phase B основного плана |
| Ranking logic | Основной план, §7 |
| Aggregation policy | Основной план, §5 |
| Gates (step-level, candidate-level) | Основной план, §6 |
| XLSX export целиком | Основной план, §10 |
| Grid enumeration, grid_point_id | Основной план, §4 |
| Status model (полная) | Основной план, §3 |
| Config schema, validation | Основной план, §1 (Этап 1) |

Spec касается **только** warmup / prepend execution semantics: как формировать extended slice, как запускать backtest, как trim-ить до OOS-only, как пересчитать метрики, как фильтровать trades, какие инварианты выполнять.

---

## W.14. Интеграция с основным планом

### W.14.1. Ссылки из основного плана на Appendix W

Следующие разделы основного плана должны ссылаться на Appendix W:

| Раздел основного плана | Что сократить | Заменить на |
|---|---|---|
| §0.5 (Источник ranking-метрик — bar-level vs trade-level) | Оставить описание donor-контракта. Убрать обсуждение prepend-поведения. | Ссылка: «Prepend-контракт и trade-level override после trim — см. Appendix W, §W.4.2–W.4.3». |
| §2.2 (Step-level long DataFrame) | Поля `prepend_bars`, `warmup_used`, `warmup_effective` — краткое упоминание. | Ссылка: «Полный перечень prepend-диагностических полей — см. Appendix W, §W.8». |
| §3.1 (Step status) | Упоминание `insufficient_bars`, `no_trades`. | Ссылка: «Влияние warmup/prepend на step status — см. Appendix W, §W.11». |
| §5.6.1–5.6.5 (Runtime / invariant checks) | Инварианты, связанные с OOS window length, array alignment. | Ссылка: «Prepend-специфичные инварианты — см. Appendix W, §W.9». |
| §9.1, шаг [6b] (Pipeline — OOS backtest) | Описание OOS execution. | Ссылка: «Canonical OOS prepend path и fallback — см. Appendix W, §W.4–W.5». |
| §12 (Требования к тестам) | Prepend-тесты. | Ссылка: «Prepend test suite — см. Appendix W, §W.12». |
| §14, Этап 3 (WF runner + step executor) | Описание step executor. | Ссылка: «Полная спецификация OOS prepend/fallback в step_executor — см. Appendix W». |

### W.14.2. Что не заменяется

Appendix W **не заменяет** основной план — он дополняет его. Основной план сохраняет:
- Полное описание pipeline (§9)
- Архитектуру модулей (§1)
- Контракты данных (§2) — за исключением prepend-полей, вынесенных в Appendix W
- Status model, gates, ranking, aggregation, export
- Phase B

---

*Конец документа.*
