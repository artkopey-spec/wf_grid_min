# ТЗ: Post-FSM Direction Inversion — Mode D wakeup (tester-only)

**Версия:** 1.1-final  
**Статус:** готово к написанию implementation plan после архитектурного аудита

---

## 1. Цель и гипотеза

Проверить гипотезу: текущие FSM entry/exit события Mode D / wakeup полезны, но **итоговое направление позиции инвертировано** — стратегия в normal-режиме даёт убыток, а при инверсии знака итоговых позиций — прибыль.

Ввести режим, который **строго post-FSM** инвертирует итоговый position stream перед расчётом returns/PnL/trades. FSM работает без изменений; меняется только знак `filtered_positions` после его завершения.

Задача — минимальная инженерная работа под разовую проверку гипотезы в tester-пайплайне.

---

## 2. Контекст архитектуры

### 2.1. Ключевые файлы

| Файл | Роль |
|---|---|
| `donor/supertrend_optimizer/core/trade_filter_config.py` | Shared schema (dataclasses), парсер `build_trade_filter_config_from_raw`, валидатор `validate_trade_filter`, `TRADE_FILTER_ALLOWED_KEYS` |
| `donor/supertrend_optimizer/core/zigzag_st_filter.py` | FSM `apply()` — единственный источник `filtered_positions` |
| `donor/supertrend_optimizer/core/backtest.py` | Пайплайн: `apply()` → `calculate_returns()` → `extract_trades()` |
| `donor/supertrend_optimizer/cli/tester.py` | Точка входа tester; вызывает `collect_trade_filter_unknown_keys`, `build_trade_filter_config_from_raw`, `validate_trade_filter` |

### 2.2. Пайплайн (упрощённо)

```
YAML config
  → collect_trade_filter_unknown_keys (→ TRADE_FILTER_ALLOWED_KEYS)
  → build_trade_filter_config_from_raw (→ dataclasses)
  → validate_trade_filter
  → zigzag_st_filter.apply()          ← точка внедрения инверсии
      FSM loop → filtered_positions
      [POST-FSM: sign inversion здесь]
      return ZigZagSTFilterResult(positions=...)
  → calculate_returns(positions)
  → extract_trades(positions)
  → metrics, XLSX export
```

### 2.3. Shared core = общий для tester и wf_grid

`wf_grid` использует те же dataclasses и тот же `build_trade_filter_config_from_raw`, но имеет собственный `_ALLOWED_KEYS` (`wf_grid/config/loader.py`). Для wakeup-поддерева в текущем коде есть drift-guard: shared `TRADE_FILTER_ALLOWED_KEYS` и `wf_grid._ALLOWED_KEYS` должны оставаться синхронными.

Поэтому `direction_mode` добавляется **и** в shared allowlist, **и** в `wf_grid/config/loader.py _ALLOWED_KEYS`. Это не делает Mode D / wakeup допустимым в WF Grid: существующий `validate_trade_filter(..., caller_pipeline="wf_grid")` по-прежнему должен reject-ить весь `trade_filter.wakeup_regime` как unsupported pipeline. Runtime helper остаётся defensive guard-ом для direct/unit вызовов, а не заменой validation contract.

---

## 3. Scope

### 3.1. Входит

1. Поле `direction_mode` в dataclass `TradeFilterWakeupEntryConfig`.
2. Парсинг поля (default `"normal"`).
3. Добавление `"direction_mode"` в `TRADE_FILTER_ALLOWED_KEYS["trade_filter.wakeup_regime.entry"]`.
4. Добавление `"direction_mode"` в `wf_grid/config/loader.py _ALLOWED_KEYS["trade_filter.wakeup_regime.entry"]` для сохранения drift-guard контракта.
5. Minimal валидация: literal/type reject.
6. Helper `apply_wakeup_direction_mode` + вызов в `zigzag_st_filter.apply()`.

### 3.2. Явно НЕ входит

- Per-bar diagnostic поле `wakeup_direction_mode` — **не добавляем** (→ strict keyset/dtype контракты diagnostics и XLSX-схема не трогаются).
- Dedicated scalar echo `direction_mode` в XLSX / tester summary / metrics / diagnostics — **не добавляем**. Raw YAML snapshot в `Tester_Config` может содержать ключ, если он присутствовал в исходном YAML; это не считается новым echo-полем.
- Новый semantic gating именно для `direction_mode` — **не добавляем**. Уже существующие правила Mode D / wakeup legality сохраняются: `wakeup_regime` допустим только в tester Mode D, а WF Grid продолжает reject-ить wakeup pipeline.

---

## 4. Главный запрет

Не адаптировать exits, stops, cycle lifecycle или ST-flip логику под inverse.

Это не новая стратегия и не inverse-FSM:

```
FSM normal positions -> post-FSM sign inversion -> backtest metrics
```

---

## 5. Что не меняется

Следующие компоненты FSM остаются без изменений при `direction_mode: inverse`:

entry filters, candidate height/age/direction, TTL, local median stop, cycle take profit, max trades per cycle, lock cycle direction, position freeze, ST-flip timing, lifecycle FSM, cycle logic, execution model, commissions, warmup, early entry/exit timing.

---

## 6. Новый параметр

Относится **только** к `wakeup_regime` / Mode D:

```yaml
trade_filter:
  wakeup_regime:
    entry:
      direction_mode: normal   # normal | inverse
```

**Default:** `normal`. Отсутствующий ключ эквивалентен `normal`.

Примечание по именованию: семантически это post-FSM инверсия всего position stream, а не entry-level атрибут. Поле размещается под `entry` для структурной согласованности.

---

## 7. Config Contract

### 7.1. Dataclass

Файл: `donor/supertrend_optimizer/core/trade_filter_config.py`, класс `TradeFilterWakeupEntryConfig` (~строка 279).

Добавить поле:

```python
direction_mode: object = "normal"
```

Тип `object` (а не `str`) — чтобы non-string значения дошли до валидатора и отвергались единообразно с остальными полями блока, по уже сложившемуся паттерну.

После изменения класс выглядит так:

```python
@dataclass
class TradeFilterWakeupEntryConfig:
    candidate_height: TradeFilterWakeupCandidateHeightConfig = field(...)
    candidate_age: TradeFilterWakeupCandidateAgeConfig = field(...)
    atr_expansion: TradeFilterWakeupAtrExpansionConfig = field(...)
    volume_expansion: TradeFilterWakeupVolumeExpansionConfig = field(...)
    direction_mode: object = "normal"   # ← новое
```

### 7.2. Парсер

Файл: `build_trade_filter_config_from_raw` (~строка 794), в конструкторе `TradeFilterWakeupEntryConfig`:

```python
entry=TradeFilterWakeupEntryConfig(
    candidate_height=...,
    candidate_age=...,
    atr_expansion=...,
    volume_expansion=...,
    direction_mode=entry_raw.get("direction_mode", "normal"),   # ← новое
),
```

### 7.3. Allowlist

Файл: `TRADE_FILTER_ALLOWED_KEYS` (~строка 567):

```python
"trade_filter.wakeup_regime.entry": frozenset({
    "candidate_height", "candidate_age", "atr_expansion",
    "volume_expansion",
    "direction_mode",  # ← добавить
}),
```

Файл: `wf_grid/config/loader.py`, `_ALLOWED_KEYS` (~строки 248-253):

```python
"trade_filter.wakeup_regime.entry": {
    "candidate_height", "candidate_age", "atr_expansion",
    "volume_expansion",
    "direction_mode",  # ← добавить для синхронизации с shared allowlist
},
```

WF Grid runtime semantics не меняются: конфиг с `trade_filter.wakeup_regime` всё равно должен быть отклонён через caller pipeline validation.

---

## 8. Валидация

Файл: `_validate_wakeup_regime_block` (~строка 1849).

Правила для `direction_mode`:

- non-string значение (int, bool, None-explicit и т.п.) → config validation error;
- строка вне `{"normal", "inverse"}` → config validation error;
- отсутствующий ключ → `"normal"` (обеспечено парсером, дополнительной проверки не требует);
- `"normal"` и `"inverse"` → accepted.

Новый gating вида "`direction_mode: inverse` сам по себе требует Mode D" **не добавляется**. При этом существующие cross-field rules остаются действующими:

- `trade_filter.wakeup_regime` allowed only with `trade_filter.zigzag.mode: D`;
- `trade_filter.zigzag.mode: D` requires `wakeup_regime.enabled: true`;
- WF Grid rejects `wakeup_regime` независимо от `direction_mode`.

Helper guard в runtime нужен только для defensive/direct calls и не должен противоречить validation path.

Проверку добавлять **только когда** ключ `("trade_filter", "wakeup_regime", "entry", "direction_mode")` присутствует в `raw_user_keys` — согласно паттерну остальных необязательных полей блока.

---

## 9. Runtime Semantics

### 9.1. `normal`

Текущее поведение без изменений. Результат бит-в-бит идентичен прогону без ключа `direction_mode`.

### 9.2. `inverse`

FSM отрабатывает полностью как сейчас. После построения полного `filtered_positions` применяется:

```python
filtered_positions = apply_wakeup_direction_mode(
    filtered_positions,
    trade_filter_config,
    mode_d_enabled=mode_d_enabled,
)
```

где `apply_wakeup_direction_mode` выполняет:

```python
filtered_positions = -filtered_positions   # единственное действие
```

Далее инвертированные `filtered_positions` без изменений уходят в `calculate_returns()`, `extract_trades()`, metrics — никакие downstream-функции не модифицируются.

### 9.3. Helper

```python
def apply_wakeup_direction_mode(
    positions: np.ndarray,
    trade_filter_config: Any,
    *,
    mode_d_enabled: bool,
) -> np.ndarray:
```

**Требования:**

- инвертирует (`-positions`) **только** при одновременном выполнении всех условий:
  - `mode_d_enabled == True`;
  - `wakeup_regime.enabled == True`;
  - `wakeup_regime.entry.direction_mode == "inverse"`.
- при любом другом случае (mode ≠ D, wakeup disabled, `direction_mode != "inverse"`, отсутствие/None config) — возвращает `positions` **без изменений, не бросая исключения**;
- helper **не резолвит ZigZag mode заново** из config. В `apply()` effective mode уже вычислен из `zigzag_global_stats.zigzag_mode`; повторная резолюция из duck-typed config может разойтись с фактическим runtime mode;
- defensive-поведение обязательно: `apply()` вызывается из tester, unit-тестов и WF Grid — guard не должен полагаться на предварительную отработку валидатора;
- возвращает dtype-compatible результат: при `np.int8` и значениях `{-1, 0, 1}` переполнения нет, `-positions` сохраняет dtype;
- возвращает **новый** массив (или safe-modify), не мутирует вход.

### 9.4. Место вызова в `apply()`

Файл: `donor/supertrend_optimizer/core/zigzag_st_filter.py`, функция `apply()`.

```
... FSM loop завершён ...
filtered_positions построен полностью

# ← ЕДИНСТВЕННАЯ точка вызова
filtered_positions = apply_wakeup_direction_mode(
    filtered_positions,
    trade_filter_config,
    mode_d_enabled=mode_d_enabled,
)

if not diag_enabled:                     # ← строка ~3990
    return ZigZagSTFilterResult(...)

return _finalize_apply_result(...)        # ← строка ~4001
```

Единая точка до развилки `diag_enabled` покрывает обе ветки (`collect_filter_diagnostics=True` и `False`) без дублирования.

---

## 10. Инварианты

### 10.1. Знаковые

При `inverse`, для одного и того же config/данных/параметров backtest:

```python
positions_inverse == -positions_normal
```

- `LONG (+1) → SHORT (-1)`;
- `SHORT (-1) → LONG (+1)`;
- `FLAT (0) → FLAT (0)` (zeros сохраняются);
- entry/exit bars не сдвигаются;
- per-bar diagnostics остаются pre-inversion;
- trade-attached diagnostics (`entry_filter_state`, `entry_trigger_source`, `exit_reason`, `wakeup_position_action`, `supertrend_color` и аналогичные поля) остаются диагностическими причинами исходного FSM (pre-inversion);
- trade `direction`, `gross_pnl_pct`, `net_pnl_pct`, `returns`, equity и metrics строятся по post-inversion positions.

### 10.2. Предусловие инварианта «число transitions / сделок не меняется»

Гарантируется **только при `early_exit_enabled = false`**.

При включённом early exit `exit_bar` считается по `equity_curve`, построенной на инвертированных returns — другая кривая → другой `exit_bar` → другое число баров/сделок. В таком режиме inverse имеет самостоятельную семантику и не сравнивается с normal по числу сделок.

### 10.3. Экономические следствия (намеренные, не чинить)

Exit-экономика (`cycle_take_profit`, `local_median_stop`, `max_trades_per_cycle`) принимает решения внутри FSM по **не-инвертированному** PnL. В inverse это означает: take-profit срабатывает по прибыли исходного FSM-направления = по убытку инверсной позиции. Это намеренно — inverse является диагностическим экспериментом, а не торгуемой стратегией.

Trade-level `supertrend_color` остаётся pre-inversion (диагностический ST-цвет из `trend`), тогда как `direction`/`gross_pnl_pct` сделки — post-inversion. Рассинхрон `direction` vs `supertrend_color` в inverse ожидаем и не правится.

Trade-equity MaxDD в inverse — самостоятельная величина, **не** зеркало MaxDD normal (equity строится накоплением, не линейна).

---

## 11. Acceptance Criteria

### AC-1: `direction_mode: normal` — parity

Trading outputs при `direction_mode: normal` (или отсутствующем ключе) полностью совпадают с текущим baseline:

- `positions`, `returns`, `trades`, `metrics` — бит-в-бит без изменений;
- изменений в diagnostics/export схеме **нет** (diagnostic/dedicated echo поля в scope не входят);
- raw YAML snapshot в XLSX может содержать `direction_mode`, если пользователь явно добавил ключ в YAML. Это не является изменением export schema.

### AC-2: `direction_mode: inverse` — зеркальность (при `commission=0`, `early_exit_enabled=false`)

Для одного и того же config/данных/параметров, на API-level результате полного 100% прогона до XLSX post-processing / segment filtering:

- `positions_inverse == -positions_normal`;
- все `0` остаются `0`;
- количество position transitions совпадает;
- количество trades совпадает;
- `entry_index[i]` совпадает для каждой сделки i;
- `exit_index[i]` совпадает для каждой сделки i;
- `direction[i]` инвертирован (`LONG ↔ SHORT`) для каждой сделки i;
- `gross_pnl_pct_inverse[i] == -gross_pnl_pct_normal[i]` (точно до float-округления в `round(..., 6)`).

### AC-3: Mirror-прогон (target config, slow/manual regression)

**Пины прогона:**

| Параметр | Значение |
|---|---|
| config | `C:\3.1_wf_grid\config_tester.yaml` |
| dataset | `C:\3.1_wf_grid\data.csv` |
| dataset sha256 | `69e0bef879845e300628ea8c4fd7ceb309d363cdff6265f850f38a115a8e4a88` |
| dataset total_lines | `297 220` (1 header + 297 219 баров) |
| dataset range | `2025-05-05T07:00 — 2026-05-08T23:49`, `+03:00`, 1-минутная |
| commission | `0.0` |
| early_exit.enabled | `false` |
| execution_model | `open_to_open` |
| trade_mode | `revers` |
| supertrend | atr_period `50`, multiplier `2.5` |
| warmup | auto → effective `400` |
| segmentation | legacy, n_parts `7` |

**Inverse arm** = тот же конфиг + единственное добавление:

```yaml
trade_filter:
  wakeup_regime:
    entry:
      direction_mode: inverse
```

**Ожидаемые метрики inverse arm** (по единому `Trades_100` — все 10 943 сделки полного прогона):

| Метрика | Ожидание | Tolerance |
|---|---|---|
| `sum(gross_pnl_pct)` | `+158.1656 п.п.` | абс. ±0.05 п.п. |
| `mean(gross_pnl_pct)` | `+0.01445 п.п.` | отн. ±5% |
| `median(gross_pnl_pct)` | `+0.03464 п.п.` | отн. ±5% |
| trade-equity MaxDD | `−5.80 п.п.` | абс. ±0.05 п.п. |

**Определения метрик:**

- `sum / mean / median` — по `trades_df.gross_pnl_pct` единого сквозного списка сделок полного прогона (`_100` = 100% датасета).
- **Trade-equity** = `cumulative sum(gross_pnl_pct)` по сделкам в хронологическом порядке, начало с `0`. При `commission=0` → `gross_pnl_pct = net_pnl_pct`.
- **Trade-equity MaxDD** = `min(trade_equity - running_max(trade_equity))` — абсолютная просадка в процентных пунктах. **Не** bar-level ratio-based MaxDD из `Metrics_100` (та величина иная: `−79.95%` для baseline normal).
- Числа зафиксированы для данного снапшота `data.csv` (sha256 выше); при изменении датасета AC-3 требует пересчёта.

**Зеркальная сверка normal ↔ inverse** (primary automated assertion, независима от датасета):

- baseline normal даёт `sum(gross_pnl_pct) ≈ −158.1656 п.п.`;
- `sum_inverse = -sum_normal` (следствие AC-2).

---

## 12. Тесты

### T-1: Unit — helper

```python
import numpy as np
inp = np.array([0, 1, 1, -1, -1, 0], dtype=np.int8)
```

Assertions:
- при `direction_mode="inverse"` и `mode_d_enabled=True` (valid Mode D wakeup config): результат `np.array([0, -1, -1, 1, 1, 0], dtype=np.int8)`;
- dtype результата == `np.int8`;
- zeros в результате == zeros во входе;
- при `direction_mode="normal"`: результат идентичен входу (`positions_normal == inp`);
- при `trade_filter_config=None`: возвращает `inp` без исключения;
- при `mode_d_enabled=False`: возвращает `inp` без исключения;
- при `wakeup_regime.enabled=False`: возвращает `inp` без исключения.

### T-2: Config

| Случай | Ожидание |
|---|---|
| ключ `direction_mode` отсутствует в YAML | `entry.direction_mode == "normal"` |
| `direction_mode: normal` | accepted |
| `direction_mode: inverse` | accepted |
| `direction_mode: invers` (опечатка) | config validation error |
| `direction_mode: x` | config validation error |
| `direction_mode: 1` (int) | config validation error |
| `direction_mode: true` (bool) | config validation error |

Новые gating-тесты только для `direction_mode` не пишем. Существующие Phase0-тесты legality для Mode D / wakeup / WF Grid должны продолжать проходить без изменения смысла.

### T-3: Integration — normal vs inverse

Один полный 100% backtest в обоих режимах с `commission=0`, `early_exit_enabled=false`, до XLSX export и без segment/OOS post-filtering.

Assertions (соответствуют AC-2):
- `np.array_equal(positions_inverse, -positions_normal)`;
- `len(trades_inverse) == len(trades_normal)`;
- все `entry_index` совпадают попарно;
- все `exit_index` совпадают попарно;
- все `direction` инвертированы попарно (`LONG ↔ SHORT`);
- `np.allclose(trades_inverse.gross_pnl_pct, -trades_normal.gross_pnl_pct, atol=1e-5)`.

### T-4: Regression — normal parity

Существующий golden/baseline config (без `direction_mode` или с `direction_mode: normal`) даёт прежние trading metrics — positions, returns, trades, metrics идентичны pre-change baseline (AC-1).

---

## 13. Файлы с изменениями (summary)

| Файл | Тип изменения |
|---|---|
| `donor/supertrend_optimizer/core/trade_filter_config.py` | Аддитивно: поле в dataclass + парсер + allowlist + validation rule |
| `wf_grid/config/loader.py` | Аддитивно: синхронизация `_ALLOWED_KEYS["trade_filter.wakeup_regime.entry"]` с shared allowlist; WF Grid rejection semantics не меняются |
| `donor/supertrend_optimizer/core/zigzag_st_filter.py` | Аддитивно: helper function + один вызов перед return-ветками |
| `donor/supertrend_optimizer/tests/` или `donor TESTER/tests/` | Новые тесты T-1..T-4 |

Файлы **не трогаем**: `wf_grid/config/schema.py`, любые `io/excel_tester.py`, `testing/runner.py`, `io/diagnostics_v2.py`, `core/backtest.py`, `core/trades.py`, `core/metrics.py`.
