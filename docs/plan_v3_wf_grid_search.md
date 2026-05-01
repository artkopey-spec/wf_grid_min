# ПЛАН РЕАЛИЗАЦИИ v3: WF Grid Search

Версия: 3.0  
Дата: 2026-03-31

Приложения:
- `C:\3.0\docs\appendix_w_warmup_prepend_spec.md`
- `C:\3.0\docs\xlsx_export_spec.md`

---

## 0. База и рамки

### 0.1. Что переиспользуем из донора

- `load_config`, `load_ohlc_csv`, `validate_ohlc_data`, `make_walk_forward_slices`, `calculate_warmup`, `apply_auto_warmup_to_config`.
- `run_single_backtest` + `BacktestResult` как единый контракт исполнения.
- `calculate_all_metrics` и `extract_trades` — через `run_single_backtest`.
- Excel helper-функции из `REFERENCE_excel_wf_sheets_excerpt.py` через adapter-слой.
- Prepend-семантика OOS берётся как execution contract из `walk_forward.py` + `test_oos_prepend.py`, формально фиксирована в `appendix_w_warmup_prepend_spec.md`.

### 0.2. Что реализуем в grid

- Grid enumeration + deterministic identity.
- WF runner для каждой grid point.
- Сбор step/train/OOS long-слоёв.
- Aggregation, gates, scoring, ranking.
- Export book и summary builder.
- Orchestrator + diagnostics/logging.

### 0.3. Контракты метрик (ключевое)

- `INVALID_METRIC_VALUE = -999.0` — invalid sentinel.
- `MAX_VALID_METRIC = 9999.0` — capped extreme, **не** invalid.
- При `extract_trades_flag=True` trade-level метрики (`sum_pnl_pct`, `num_trades`, `win_rate`, `profit_factor`, `avg_trade`) — authoritative.
- Ratio-метрики (`sharpe`, `sortino`, `cagr`, `max_drawdown`) — bar-level.
- **Знаковое соглашение `max_drawdown`**: ≤ 0.0.  
  Значение `-0.15` означает просадку 15% от пика. Ноль — нет просадки.  
  Соглашение наследуется из donor (`core/metrics.py: calculate_max_drawdown`):  
  `drawdown = equity / peak - 1`, `max_dd = min(drawdown)`.
  - **Aggregation**: `max_drawdown_Min` = наихудшая (самая отрицательная) просадка среди OOS-сегментов; `max_drawdown_Max` = наилучшая (ближайшая к нулю).
  - **Gates**: оператор `>=` (правая граница — отрицательный порог); пример: `max_drawdown >= -0.30` — «просадка не хуже 30%».
  - **`abs_max_drawdown_Min`** (для scoring §7.3): `abs(max_drawdown_Min)`; higher = worse → `lower_is_better`.

---

## 1. Архитектура модулей

```text
wf_grid/
├── config/
│   ├── schema.py
│   └── loader.py
├── grid/
│   └── enumeration.py
├── wf/
│   ├── runner.py
│   └── step_executor.py
├── collect/
│   ├── step_collector.py
│   └── trades_collector.py
├── aggregate/
│   └── aggregator.py
├── gates/
│   └── gates.py
├── ranking/
│   ├── scoring.py
│   ├── tiering.py
│   └── ranker.py
├── export/
│   ├── summary_builder.py
│   └── xlsx_writer.py
├── status/
│   └── status_model.py
├── logging/
│   └── diagnostics.py
├── pipeline/
│   └── orchestrator.py
└── tests/
```

`wf/step_executor.py` реализует prepend/fallback строго по `appendix_w_warmup_prepend_spec.md`.

### 1.1. Config schema (Phase A)

Единый реестр config-полей, используемых pipeline.  
Все поля валидируются на шаге A1 (Config load + validation).

| Section | Key | Type | Default | Validation |
|---|---|---|---|---|
| `data` | `file_path` | str | required | file exists |
| `data` | `periods_per_year` | `"auto"` \| float | `"auto"` | см. ниже |
| `optimization` | `atr_period_range` | [int, int] | `[5, 55]` | `[min, max]`, min ≥ 2 |
| `optimization` | `multiplier_range` | [float, float] | `[1.5, 5.5]` | `[min, max]`, min > 0 |
| `optimization` | `multiplier_step` | float | `0.1` | `> 0` |
| `optimization` | `trade_mode` | str | `"both"` | enum TRADE_MODES |
| `backtest` | `commission` | float | `0.000235` | `>= 0`, finite |
| `backtest` | `min_trades_required` | int | `3` | `>= 0` |
| `backtest` | `early_exit_enabled` | bool | `true` | — |
| `backtest` | `early_exit_max_drawdown` | float | `0.50` | `> 0` |
| `backtest` | `early_exit_check_bars` | int | `50` | `>= 0` |
| `validation` | `warmup_period` | int | `0` | `>= 0` |
| `validation` | `warmup_period_auto` | bool | `false` | — |
| `validation` | `walk_forward.train_size` | str | required | duration string |
| `validation` | `walk_forward.test_size` | str | required | duration string |
| `validation` | `walk_forward.step_size` | str? | `= test_size` | duration string |
| `validation` | `walk_forward.scheme` | str | `"rolling"` | `rolling` \| `expanding` |
| `gates` | `step.min_trades` | int | `= min_trades_required` | `>= 0` |
| `gates` | `step.max_drawdown_threshold` | float | `-0.50` | `<= 0` |
| `gates` | `candidate.positive_median_threshold` | float | `0.0` | — |
| `gates` | `candidate.min_trades_median` | float | `3.0` | `>= 0` |
| `gates` | `candidate.worst_segment_pnl_threshold` | float? | `null` (disabled) | — |
| `gates` | `candidate.max_drawdown_threshold` | float | `-0.50` | `<= 0` |
| `ranking` | `mode` | str | `"legacy"` | `legacy` \| `gates_score` |
| `ranking` | `min_segments_for_ranking` | int? | `max(2, ceil(N*0.5))` | `2..n_segments` |
| `ranking` | `sort_by` | str | `"sum_pnl_pct_Median"` | valid aggregate column |
| `ranking` | `tiebreaker` | str | `"sum_pnl_pct_Min"` | valid aggregate column |
| `scoring` | `score_weights` | dict | §7.3 defaults | values > 0 |
| `status` | `min_meaningful_bars` | int | `30` | `> 0` |

**`periods_per_year` resolution:**

- `"auto"` (default): `periods_per_year = round(pd.Timedelta(days=365.25) / median_bar_delta)`. Требует DatetimeIndex. При не-DatetimeIndex без explicit override — pipeline abort с ошибкой:  
  `Cannot auto-detect periods_per_year without DatetimeIndex; set data.periods_per_year explicitly`.
- numeric (float): используется as-is. Типичные значения: 252 (дневные акции), 365 (дневные крипто), 2190 (4h), 8760 (1h).
- Resolved value записывается в `WF_Config` лист и в pipeline log.

---

## 2. Контракты данных

### 2.1. `GridPoint`

- `atr_period: int`
- `multiplier: float` (canonical)
- `trade_mode: str`
- `grid_point_id: str`

### 2.2. `step_oos_long`

Одна строка = `(grid_point_id, wf_step)`, содержит:
- identity-поля;
- оконные поля;
- `step_status`;
- OOS метрики (`sum_pnl_pct`, `sharpe`, `sortino`, `max_drawdown`, `cagr`, `win_rate`, `num_trades`, `profit_factor`, `avg_trade`);
- prepend diagnostics:
  - `prepend_bars_requested`
  - `prepend_bars_applied`
  - `used_prepend`
  - `used_legacy_oos_path`
  - `oos_boundary_index`
  - `warmup_used`
  - `warmup_effective`
  - `effective_oos_bars`

Полная семантика этих полей — в Appendix W.

### 2.3. Метрики, исключённые из Phase A

`max_losing_streak`, `max_losing_trade`, `worst_streak_pnl`  
в Phase A **не входят** в обязательные контракты, aggregation и summary.

### 2.4. `summary_wide`

Порядок и состав колонок — по `xlsx_export_spec.md`, с delta:
- в Phase A Block B без streak-колонок;
- `WF_01..WF_N` = snapshot всех grid points на шаге;
- `ok_ratio` = `n_ok_steps / n_total_steps` (float, 0.0–1.0) — доля валидных шагов. Записывается в Block A после `n_segments`.

---

## 3. Статусная модель

### 3.1. Step statuses

- `ok`
- `skipped`
- `no_trades`
- `insufficient_bars`
- `invalid`
- `gate_failed`
- `runtime_error`

### 3.2. Критические правила

- `no_trades`: если финальный `num_trades == 0` после OOS trim/override.
- `insufficient_bars`: если `effective_oos_bars < min_meaningful_bars` (config, default=30).
- `gate_failed`: если step-level gate не пройден (§6.1).
- В aggregation участвуют только `ok`.

### 3.3. Candidate statuses

- `ok`: все шаги `ok`
- `partial`: есть и `ok`, и non-`ok`
- `failed`: `ok_steps == 0`

---

## 4. Grid identity и воспроизводимость

### 4.1. Каноническая enumeration multiplier

Multiplier перечисляется через integer-backed ticks, не через float accumulation.

### 4.2. Единый источник canonical multiplier

Одна и та же каноническая величина используется для:
- `GridPoint.multiplier`
- `grid_point_id`
- фактического вызова `run_single_backtest`
- экспортируемых таблиц

Это убирает cross-platform float drift и стабилизирует golden/regression тесты.

---

## 5. Aggregation policy

### 5.1. Метрики Phase A

`sum_pnl_pct`, `sharpe`, `sortino`, `max_drawdown`, `cagr`, `win_rate`, `num_trades`, `profit_factor`, `avg_trade`.

### 5.2. Маска агрегации

Только `step_status == ok`.

### 5.3. Sentinel handling

`INVALID_METRIC_VALUE -> NaN` до aggregation.

### 5.4. Capped-extreme handling

Per-metric pre-filter:
- `profit_factor == MAX_VALID_METRIC` -> `NaN`
- `sharpe == MAX_VALID_METRIC` -> `NaN`

Иначе Mean/Std/Max становятся статистически мусорными.

### 5.5. Aggregate reliability annotation

Каждая агрегатная строка содержит:
- `n_ok_steps`: число шагов с `step_status == ok` (участвовавших в aggregation).
- `n_total_steps`: общее число WF-шагов.
- `ok_ratio`: `n_ok_steps / n_total_steps`.

Эти поля не влияют на вычисление агрегатов, но доступны для downstream (gates, ranking, export, аналитик).

---

## 6. Gates

### 6.1. Step-level gates

Применяются к каждой строке `step_oos_long` с `step_status == ok`.  
При failure: `step_status` меняется на `gate_failed`.

| Gate | Метрика | Оператор | Порог (default) | Config key |
|---|---|---|---|---|
| `min_trades` | `num_trades` | `>=` | `config.backtest.min_trades_required` (default 3) | `gates.step.min_trades` |
| `max_drawdown` | `max_drawdown` | `>=` | `-0.50` (просадка не хуже 50%) | `gates.step.max_drawdown_threshold` |

### 6.2. Candidate-level gates

Применяются к агрегированным строкам `summary_wide`.  
Все проверки — `bool`. Результат каждого gate записывается как отдельная колонка.

| Gate | Формула | Default threshold | Config key |
|---|---|---|---|
| `gate_ok_positive_median` | `sum_pnl_pct_Median > threshold` | `0.0` | `gates.candidate.positive_median_threshold` |
| `gate_ok_min_trades` | `num_trades_Median >= threshold` | `3.0` | `gates.candidate.min_trades_median` |
| `gate_ok_worst_segment` | `sum_pnl_pct_Min >= threshold` | `null` (disabled) | `gates.candidate.worst_segment_pnl_threshold` |
| `gate_ok_drawdown` | `max_drawdown_Min >= threshold` | `-0.50` | `gates.candidate.max_drawdown_threshold` |

### 6.3. `seed_gate_passed` — composite

```text
seed_gate_passed = gate_ok_positive_median
                 AND gate_ok_min_trades
                 AND gate_ok_drawdown
                 AND (gate_ok_worst_segment IF configured, ELSE True)
```

`seed_gate_fail_reason`: comma-separated list of failed gate names; пустая строка если все пройдены.

---

## 7. Ranking (v3)

### 7.1. 3-tier контракт

**Tier 1 (proven):**
- `candidate_status == ok` (все WF-шаги валидны)
- `seed_gate_passed = True`
- `n_valid_segments >= min_segments_for_ranking`

**Tier 2 (viable):**
- `candidate_status in {ok, partial}`, не попавшие в Tier 1
  (включая: ok но gates failed; partial с любым gate status; insufficient valid segments для Tier 1)

**Tier 3 (failed):**
- `candidate_status == failed` (`ok_steps == 0`)

**`min_segments_for_ranking`:**
- Config key: `ranking.min_segments_for_ranking`
- Default: `max(2, ceil(n_segments * 0.5))`
- Валидация: `2 <= min_segments_for_ranking <= n_segments`
- Обоснование: кандидат в Tier 1 должен иметь валидные результаты как минимум на половине OOS-шагов. Порог 2 — абсолютный минимум, исключающий одношагового «везунчика» при любом `n_segments`.

**Design decision:** `partial` намеренно исключён из Tier 1.  
Агрегаты partial-кандидатов вычислены по подмножеству шагов (§5.2) и подвержены survivorship bias: исключённые non-ok шаги не ухудшают средние. Tier 1 зарезервирован для кандидатов с полным и непрерывным track record по всем OOS-окнам. Поле `ok_ratio` (§5.5) записывается для всех кандидатов — аналитик использует его для визуальной оценки надёжности Tier 2 кандидатов.

### 7.2. Сортировка

**Gates/score mode (`ranking_mode = gates_score`):**

Tier 1 (внутренняя сортировка):
1. `tester_seed_score` DESC
2. `sum_pnl_pct_Median` DESC (tiebreaker 1)
3. `sum_pnl_pct_Std` ASC (tiebreaker 2 — меньше волатильность лучше)
4. `grid_point_id` ASC (финальный детерминистический tiebreaker)

Tier 2 (внутренняя сортировка):
1. `sum_pnl_pct_Median` DESC
2. `sum_pnl_pct_Min` DESC
3. `sum_pnl_pct_Std` ASC
4. `grid_point_id` ASC

Tier 3 (внутренняя сортировка):
1. `grid_point_id` ASC (стабильный порядок для failed)

**Legacy mode (`ranking_mode = legacy`):**
1. `sort_by` DESC (default: `sum_pnl_pct_Median`)
2. tiebreaker DESC (default: `sum_pnl_pct_Min`)
3. `sum_pnl_pct_Std` ASC
4. `grid_point_id` ASC

NaN -> в конец при всех сортировках. Dense rank 1..G без пропусков.  
Финальный tiebreaker `grid_point_id` гарантирует абсолютную детерминированность.

### 7.3. `tester_seed_score` — weighted normalized composite score

**Формула:**

```text
tester_seed_score = w1 * norm(sum_pnl_pct_Median)
                  + w2 * norm(profitable_segments_count)
                  + w3 * norm_inv(abs(max_drawdown_Min))
```

**Веса по умолчанию:**

| Компонент | Вес | Направление |
|---|---|---|
| `sum_pnl_pct_Median` | 0.45 | higher better |
| `profitable_segments_count` | 0.35 | higher better |
| `abs(max_drawdown_Min)` | 0.20 | lower better |

**Policy:**

1. Нормализация: min-max по passed-кандидатам.
   - `norm(x) = (x - x_min) / (x_max - x_min)`, clamp to [0, 1].
   - `norm_inv(x) = 1 - norm(x)` (lower-is-better инвертируется).
   - При `x_max == x_min` -> `norm = 0.0` (нет дисперсии -> нулевой вклад, вес перераспределяется).
2. NaN handling — **redistribute per row**:
   - Для min-max scaling NaN временно заменяется медианой столбца (чтобы не искажать min/max оставшихся значений).
   - После вычисления нормализованных компонент: если исходное значение компоненты было NaN, её вес перераспределяется пропорционально на оставшиеся валидные компоненты строки.
   - Если **все** компоненты NaN -> `tester_seed_score = NaN`, `score_contract_status = "no_score"`.
   - `score_contract_status = "partial"` когда хотя бы одна (но не все) компоненты перераспределены.
   - Политика zero-out **не используется**.
3. Авто-нормализация весов: если `sum(weights) != 1.0`, делить каждый на `sum(weights)`.
4. Score вычисляется **только для `seed_gate_passed=True`** строк. Остальные получают `tester_seed_score = NaN`.
5. `score_contract_status`:
   - `"ok"` — все компоненты валидны;
   - `"partial"` — часть компонент NaN (вес перераспределён);
   - `"no_score"` — кандидат не прошёл gates.
6. Конфигурируемость: `scoring.score_weights` в config (dict metric→weight). Если колонки нет в DataFrame — она пропускается, вес перераспределяется, `score_contract_status = "partial"`.

---

## 8. Warmup / Prepend execution

Коротко: canonical OOS path в `wf/step_executor.py`:

`extended slice -> run_single_backtest -> trim to OOS -> recompute metrics (warmup=0) -> trade filter/rebase -> trade-level override`

Полная спецификация: `C:\3.0\docs\appendix_w_warmup_prepend_spec.md`.

---

## 9. Pipeline Phase A

1. Config load + validation  
2. Data load + validation  
3. `periods_per_year` resolution (auto-detect или explicit)  
4. Warmup resolution (`prepend_bars_requested`)  
5. WF slicing  
6. Grid enumeration (canonical multiplier)  
7. WF execution:
   - train path (no prepend)
   - OOS canonical prepend path (Appendix W)  
8a. Step gates (step-level, §6.1) -> update `step_status`  
8b. Step/trades collection  
9. Aggregation (+ sentinel/capped pre-filters + `ok_ratio`)  
10. Candidate gates (`gate_ok_*`, `seed_gate_passed`)  
11. Scoring (`tester_seed_score`)  
12. Ranking (3-tier + tiebreakers)  
13. Summary build  
14. Pre-export validation  
15. XLSX export  
16. Logging and artifacts

---

## 10. XLSX contract

Базовый контракт и порядок колонок: `C:\3.0\docs\xlsx_export_spec.md`.

### 10.1. Phase A deltas

- `WF_01..WF_N`: step snapshot всех grid points на шаге.
- Block B в `summary` без streak-колонок в Phase A.
- `ok_ratio` добавлен в Block A после `n_segments`.
- `tester_seed_score` — weighted normalized composite score (§7.3).
- `score_contract_status` — `"ok"` / `"partial"` / `"no_score"`.

### 10.2. Защитный лимит Excel

Перед export:
- `len(WF_Trades) <= 1_000_000`
- `len(WF_Train_Trades) <= 1_000_000`

Если лимит превышен:
- XLSX export abort с явной ошибкой/предупреждением;
- silent truncation запрещён.

---

## 11. Инварианты и diagnostics

### 11.1. Core invariants

- Полнота step-слоя: `|grid| * |wf_steps|`.
- Уникальность `(grid_point_id, wf_step)`.
- Статусы только из допустимого enum.
- Candidate step counts консистентны.
- Ranking dense и deterministic.
- `ok_ratio = n_ok_steps / n_total_steps` для каждого candidate.

### 11.2. Prepend invariants

Полный список — Appendix W (boundaries unchanged, exact trim, no leakage в metrics/trades, rebase индексов, authoritative OOS-only слой).

---

## 12. Тесты

### 12.1. Обязательные группы

- Unit: config/grid/status/agg/gates/scoring/ranking/export.
- Prepend suite: по Appendix W.
- Integration: mini-grid e2e, all-failed, no-trades exclusion, max-valid filtering, tiering, scoring NaN redistribute.
- Golden/regression: ranking snapshot, XLSX schema/value snapshot.
- Reproducibility: одинаковый output в повторных запусках.

---

## 13. Этапы внедрения (Phase A) — пошаговый план

### A1. Каркас Phase A: config + contracts + enums

**Цель:** скелет пакета `wf_grid/`, загрузка и валидация конфига, базовые типы.

**Deliverables:**
- `config/schema.py` — dataclass/Pydantic model по §1.1 (все поля, типы, defaults, validation).
- `config/loader.py` — `load_grid_config(path) -> GridConfig`; валидация всех полей; `periods_per_year` resolution (auto-detect из DatetimeIndex).
- `status/status_model.py` — enum `StepStatus` (§3.1), enum `CandidateStatus` (§3.3), enum `RankingMode` (§7.2), enum `ScoreContractStatus` (§7.3).
- Контракты метрик: constants re-export (`INVALID_METRIC_VALUE`, `MAX_VALID_METRIC`).
- `grid/enumeration.py` — заглушка `GridPoint` dataclass (§2.1).
- `tests/test_config_schema.py` — unit-тесты валидации: missing required fields, invalid ranges, periods_per_year auto + override.

**Критерий готовности:** `GridConfig` загружается из YAML, валидация отвергает невалидные конфиги, `periods_per_year` resolved корректно.

### A2. Grid enumeration + canonical identity

**Цель:** детерминированное порождение grid, стабильные `grid_point_id`.

**Deliverables:**
- `grid/enumeration.py` — `enumerate_grid(config) -> list[GridPoint]`:
  - multiplier через integer ticks: `tick = round(mult / step)`, `canonical_mult = tick * step` (§4.1).
  - `grid_point_id = f"atr{atr_period}_m{canonical_mult:.2f}_{trade_mode}"`.
- `tests/test_grid_enumeration.py` — float drift test, determinism test, id uniqueness.

**Критерий готовности:** `grid_point_id` стабилен cross-platform; grid size = `|atr_range| * |mult_ticks| * |trade_modes|`.

### A3. Status model

**Цель:** правила присвоения step и candidate статусов.

**Deliverables:**
- `status/status_model.py`:
  - `assign_step_status(metrics, effective_oos_bars, config) -> StepStatus` (§3.2).
  - `assign_candidate_status(step_statuses) -> CandidateStatus` (§3.3).
- `tests/test_status_model.py` — all transitions: ok, no_trades (num_trades=0), insufficient_bars (<30), gate_failed, runtime_error; candidate ok/partial/failed.

**Критерий готовности:** status assignment детерминирован, покрыт тестами.

### A4. WF runner + step executor (Appendix W)

**Цель:** execution core — train path + canonical OOS prepend path.

**Deliverables:**
- `wf/step_executor.py`:
  - `execute_oos_step(...)` — canonical prepend path (§W.4.2 шаги 1–11), legacy fallback (§W.5.1), defensive fallback (§W.5.2).
  - `execute_train_step(...)` — train path (§W.6).
  - Все диагностические поля (§W.8).
- `wf/runner.py`:
  - `run_wf_for_grid_point(grid_point, wf_slices, full_data, config) -> list[StepResult]`.
  - Итерация по WF-шагам, вызов executor, присвоение step_status (A3).
- `tests/test_step_executor.py` — полная prepend suite по §W.12.1–12.4.

**Критерий готовности:** все инварианты A–H (Appendix W) зелёные; canonical path, legacy fallback, defensive fallback покрыты.

### A5. Step collector + schemas

**Цель:** сбор `step_oos_long` и `step_train_long` из результатов A4.

**Deliverables:**
- `collect/step_collector.py`:
  - `collect_steps(grid_results: dict[str, list[StepResult]]) -> pd.DataFrame` — `step_oos_long` (§2.2).
  - train long аналогично.
- Validation: полнота (`|grid| * |wf_steps|`), уникальность `(grid_point_id, wf_step)` (§11.1).
- `tests/test_step_collector.py` — completeness, uniqueness, schema checks.

**Критерий готовности:** DataFrame c правильной схемой, все строки на месте.

### A6. Trades collector

**Цель:** сбор OOS и train trades в long-формат.

**Deliverables:**
- `collect/trades_collector.py`:
  - `collect_oos_trades(grid_results) -> pd.DataFrame` — с `grid_point_id`, `wf_step`, rebased indices.
  - `collect_train_trades(grid_results) -> pd.DataFrame`.
- `tests/test_trades_collector.py` — no prepend leakage (Инвариант E), rebase correctness.

**Критерий готовности:** trades DataFrames готовы для экспорта в `WF_Trades` / `WF_Train_Trades`.

### A7. Aggregation + pre-filters

**Цель:** агрегация step-level метрик в candidate-level; sentinel/capped handling; `ok_ratio`.

**Deliverables:**
- `aggregate/aggregator.py`:
  - `aggregate_candidates(step_oos_long, config) -> pd.DataFrame`:
    - Маска: `step_status == ok` (§5.2).
    - `INVALID_METRIC_VALUE -> NaN` (§5.3).
    - `profit_factor == MAX_VALID_METRIC -> NaN`; `sharpe == MAX_VALID_METRIC -> NaN` (§5.4).
    - Per-metric: Mean, Std, Min, Max, Median (§6 xlsx spec).
    - `profitable_segments_count` (§7 xlsx spec).
    - `n_ok_steps`, `n_total_steps`, `ok_ratio` (§5.5).
- `tests/test_aggregation.py` — sentinel exclusion, capped filtering, Std with <2 values, all-NaN metric, ok_ratio.

**Критерий готовности:** aggregated DataFrame корректен; MAX_VALID не отравляет агрегаты.

### A8. Gates (step + candidate + composite)

**Цель:** step-level и candidate-level gate evaluation; `seed_gate_passed`.

**Deliverables:**
- `gates/gates.py`:
  - `apply_step_gates(step_oos_long, config) -> pd.DataFrame` — обновляет `step_status` на `gate_failed` при failure (§6.1).
  - `apply_candidate_gates(summary_wide, config) -> pd.DataFrame` — вычисляет `gate_ok_*` колонки (§6.2); `seed_gate_passed` = AND logic (§6.3); `seed_gate_fail_reason`.
- `tests/test_gates.py` — each gate individually, composite AND, disabled worst_segment, negative max_drawdown sign.

**Критерий готовности:** gates consistent with §0.3 sign convention; composite logic testable.

**Порядок в pipeline:** step gates (A8) применяются **до** aggregation (A7) — `gate_failed` шаги исключаются из aggregation mask наравне с non-ok. Candidate gates — **после** aggregation.

### A9. Scoring (`tester_seed_score`)

**Цель:** вычисление composite score для passed candidates.

**Deliverables:**
- `ranking/scoring.py`:
  - `DEFAULT_SCORE_WEIGHTS = {"sum_pnl_pct_Median": 0.45, "profitable_segments_count": 0.35, "abs_max_drawdown_Min": 0.20}`
  - `LOWER_IS_BETTER = {"abs_max_drawdown_Min"}`
  - `calculate_seed_score(df, passed_mask, score_weights=None, lower_is_better=None) -> tuple[pd.Series, pd.Series]`
  - `abs_max_drawdown_Min` derived as `df["max_drawdown_Min"].abs()` if absent.
  - Min-max normalization по passed rows only.
  - NaN redistribute per row (§7.3 policy 2).
  - `score_contract_status` per row.
- `tests/test_scoring.py` — all-valid, one-NaN redistribute, all-NaN, single passed row (`min==max`), missing column, weight auto-normalize.

**Критерий готовности:** score deterministic; redistribute per row; contract_status correct.

### A10. Ranking (3-tier + tiebreakers)

**Цель:** tiering, sorting, dense rank.

**Deliverables:**
- `ranking/tiering.py`:
  - `assign_tiers(summary_wide, config) -> pd.Series[int]` — Tier 1/2/3 по §7.1.
- `ranking/ranker.py`:
  - `rank_candidates(summary_wide, config) -> pd.DataFrame` — tier assignment, per-tier sort (§7.2), dense rank 1..G, `grid_rank` as first column.
- `tests/test_ranking.py` — tier assignment (ok->T1, partial->T2, failed->T3), gates failed ok->T2, tiebreaker chain, `grid_point_id` final tiebreaker, dense rank no gaps, legacy mode, determinism across runs.

**Критерий готовности:** ranking deterministic; regression snapshot stable.

### A11. Summary builder

**Цель:** long->wide трансформация; Block A/B/C ordering.

**Deliverables:**
- `export/summary_builder.py`:
  - `build_summary_wide(step_oos_long, aggregated, ranked, config) -> pd.DataFrame`:
    - Segment columns `S1_*..SN_*` (xlsx spec §5).
    - Aggregate columns `{m}_Mean/_Std/_Min/_Max/_Median` (xlsx spec §6).
    - Decision columns Block A (xlsx spec §10.1), включая `ok_ratio`.
    - Key summaries Block B (xlsx spec §10.2, без streak в Phase A).
    - Remaining Block C (xlsx spec §10.3).
- `tests/test_summary_builder.py` — column order, segment count, Block A/B/C boundaries.

**Критерий готовности:** wide DataFrame прошла schema validation; column order matches spec.

### A12. Export + XLSX + row-limit guard

**Цель:** запись полной XLSX-книги по §10.

**Deliverables:**
- `export/xlsx_writer.py`:
  - `export_workbook(summary_wide, step_oos_long, trades_oos, trades_train, config, wf_slices, gates_result) -> Path`:
    - Листы: `WF_Config`, `WF_Gates` (если есть), `WF_01..WF_N`, `WF_Trades`, `WF_Train_Trades`, `summary` — по xlsx spec §2.
    - Row-limit guard: `len(trades) <= 1_000_000`; abort if exceeded (§10.2).
    - Auto-filter, freeze panes (xlsx spec §11).
    - resolved `periods_per_year` в `WF_Config`.
- `tests/test_xlsx_export.py` — schema snapshot, row-limit abort, all sheets present, column order.

**Критерий готовности:** книга открывается в Excel; golden snapshot на mini-grid.

### A13. Orchestrator + logging + стабилизация

**Цель:** склейка шагов A1–A12 в единый pipeline; diagnostics.

**Deliverables:**
- `pipeline/orchestrator.py`:
  - `run_grid_pipeline(config_path: str) -> PipelineResult`:
    - Шаги 1–16 из §9 последовательно.
    - Progress logging: grid size, current step, timing.
    - Error handling: per-grid-point isolation (`runtime_error` не роняет pipeline).
- `logging/diagnostics.py` — summary log: grid size, step status distribution, tier distribution, top-5 ranked, timing breakdown.
- `tests/test_orchestrator.py` — e2e mini-grid (3 grid points, 3 WF steps), all-failed grid, reproducibility (2 runs -> identical XLSX).

**Критерий готовности:** pipeline стабильно исполняется end-to-end; все criteria из §14 зелёные.

---

## 14. Definition of Done (Phase A)

Считается завершённым, если:
- pipeline стабильно исполняется end-to-end;
- prepend contract соблюдён (Appendix W);
- `no_trades` и `insufficient_bars` корректно исключаются из aggregation;
- `MAX_VALID_METRIC` не отравляет агрегаты PF/Sharpe;
- `max_drawdown` sign convention (≤ 0) соблюдён во всех слоях;
- gates определены, parametrized, `seed_gate_passed` = AND logic;
- `tester_seed_score` deterministic, redistribute per row;
- ranking tiered, deterministic (финальный tiebreaker `grid_point_id`);
- `candidate_status == ok` required для Tier 1;
- `ok_ratio` присутствует в summary;
- `periods_per_year` resolved (auto или explicit);
- XLSX export соответствует `xlsx_export_spec.md` + Phase A deltas;
- row-limit guard работает без silent truncation;
- reproducibility и regression tests зелёные.

---

## 15. Что не входит в Phase A

- streak-метрики (`max_losing_streak`, `max_losing_trade`, `worst_streak_pnl`);
- TOP-K / consensus / mini-grid / refine;
- enriched scoring/channels Phase B;
- parallelism / multiprocessing (Phase A — serial).

---

## 16. Execution contract (итог)

1. Все функции typed, ошибки через exceptions.
2. `grid_point_id` присутствует во всех слоях.
3. OOS downstream использует только OOS-only слой после trim.
4. Aggregation: только `ok` шаги.
5. `INVALID_METRIC_VALUE` и `MAX_VALID_METRIC` обрабатываются разными политиками.
6. `max_drawdown` ≤ 0 во всех слоях; gates используют оператор `>=`.
7. Gates: step-level (§6.1) + candidate-level (§6.2) + composite AND (§6.3).
8. Scoring: weighted normalized composite, redistribute per row, only passed.
9. Ranking — 3-tier (ok->T1, partial->T2, failed->T3) + dense rank + deterministic tiebreakers.
10. XLSX — по `xlsx_export_spec.md`, с явным row-limit guard.
11. Warmup/prepend — строго по `appendix_w_warmup_prepend_spec.md`.
12. `periods_per_year` — resolved (auto/explicit), записан в `WF_Config`.

