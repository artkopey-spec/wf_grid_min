# Wakeup Regime Phase 0 — Implementation Plan v5.1

> Предыдущие версии: v1–v4 хранятся в `docs/` для истории. Этот документ —
> самостоятельный, готовый к реализации; ссылок на предыдущие планы не требует.

---

## 1. Цель и критерий успеха

Реализовать прототип `wakeup_regime` для `donor`-tester и проверить гипотезу:

```text
свежий price impulse + ATR expansion + volume expansion
-> немедленный вход в направлении ZigZag candidate leg (без ожидания SuperTrend flip)
-> ограниченное окно приёма новых входов
-> завершение окна, когда импульс устарел
```

**Семантика «окна» (зафиксировано как дизайн-решение):**

Когда Exit C-условие срабатывает, действие определяется `wakeup_regime.exit.action.mode`:

- `block_new_entries` — переход в `ST_STOPPING`: новые входы/реверсы запрещены,
  текущая позиция удерживается до `opposite_st_flip` или reset.
- `close_position` — текущая позиция закрывается по open-to-open на t+1,
  FSM → `OFF`, counters сбрасываются. Ожидания ST-flip нет.
  После OFF новое окно может открыться на следующем квалифицирующем баре.

`force_flat` (глобальный flatten всей системы) запрещён. `close_position` —
per-cycle lifecycle-выход wakeup-окна, не глобальный flatten.

**Критерий успеха (минимальный):**

- single-config tester запускает `zigzag.mode: D`;
- входы открываются по `candidate_leg_direction` без ожидания ST-flip;
- `wakeup_starts_count > 0`;
- Exit C (TTL / no_fresh) корректно применяет выбранный `action.mode`;
- `block_new_entries`: opposite ST-flip закрывает позицию без разворота;
- `close_position`: позиция закрывается сразу после срабатывания Exit C;
- поведение legacy-режимов A/B/C/A+B/C+B и legacy-exits A/B не меняется;
- wf_grid отвергает Mode D / exit C / wakeup_regime.

---

## 2. Source-of-truth дерево и Scope

**Единственное рабочее дерево — `donor/supertrend_optimizer/`.**
Его запускает single-config tester (`run_tester_single_config_mode.bat`
ставит `PYTHONPATH=...\donor`). Каталог `donor TESTER/` в Phase 0
**не трогаем и не используем для верификации** (устаревший mirror без
`zigzag_st_filter.py`). Все правки и все новые тесты — только в `donor/`
и `wf_grid/tests/` (reject-тесты).

**Разрешено менять:**

```text
donor/supertrend_optimizer/core/trade_filter_config.py
donor/supertrend_optimizer/core/zigzag_st_filter.py
donor/supertrend_optimizer/core/backtest.py
donor/supertrend_optimizer/engine/run.py
donor/supertrend_optimizer/testing/runner.py
donor/supertrend_optimizer/testing/signal_events.py
donor/supertrend_optimizer/io/excel_tester.py
donor/supertrend_optimizer/cli/tester.py
donor/<тесты рядом с пакетом donor>
wf_grid/tests/* (только reject/schema на shared-валидатор)
```

Точечные правки `wf_grid` разрешены только там, где он уже вызывает
shared-валидатор donor. Runtime-поддержка Mode D в `wf_grid` запрещена.

**Запрещено в Phase 0:**

```text
WF Grid runtime для Mode D
WF export wakeup-полей
изменения parallel/transport
WF OOS
production effective-config tracking
force_flat (глобальный flatten)
редизайн trade-level exit_reason (кроме обратносовместимых добавлений)
structural_compression exit (вынесен в §24 Deferred)
```

---

## 3. Архитектурный принцип: ранний dispatch

Mode D изолирован от legacy FSM-цикла. Legacy-цикл для Mode D **не
исполняется**; `_try_lifecycle_start_from_off()` для `"D"` не вызывается.
Это исключает взаимодействие с WAIT, legacy median-stop, Exit A/B
и старым volume-gate.

**Точка-якорь dispatch в `apply()`** (`core/zigzag_st_filter.py`):
сразу после того как готовы, и **до** главного `for t in range(n)`:

- `resolved_mode` (из `zigzag_global_stats.zigzag_mode`);
- `per_bar` и его v3-поля `candidate_age_bars`, `candidate_leg_direction`;
- `combined_reset_event`, `daily_reset_event`,
  `time_filter_in_window`, `time_filter_reset_event`;
- `high`, `low`, `volume` (см. §8);
- финитные `candidate_trigger_threshold`, `global_median`
  (существующая проверка остаётся; для Mode D ctt — numeric, см. §5.2).

```text
apply():
  ... общий pre-loop setup (reset events, time_filter, combined_reset, per_bar) ...
  resolved_mode = stats.zigzag_mode
  if resolved_mode == "D":
      return _run_wakeup_fsm(<готовые входы>)
  ... аллокация legacy-массивов и legacy FSM loop без изменений ...
```

`_run_wakeup_fsm` строит **собственный** набор диагностики
(включая `exit_off_mode = "exit C"`) и не переиспользует legacy-эхо.
Legacy-цикл и его массивы остаются нетронутыми.

**Разрешено переиспользовать только:**
open-to-open запись позиции; общий reset-wipe;
`_trade_mode_allows_direction`. Полный рефакторинг legacy-цикла запрещён.

---

## 4. Существующие контракты, которые нельзя ломать

- `wf_grid/config/loader.py` делегирует валидацию в
  `core/trade_filter_config.py` с `caller_pipeline="wf_grid"`;
  unknown-key проверка использует тот же shared-коллектор.
- `apply()` владеет FSM/позициями/диагностикой для A/B/C/A+B/C+B.
- Tester summary и Excel ожидают общий keyset диагностики
  (`trade_filter_state`, `trade_filter_trigger_source`,
  `filter_allowed_entry`, `filter_block_reason`,
  `median_stop_triggered` и т.д.).
- `ZigZag_Trigger_Events` строится по правилу
  `trigger_source[t] != "none"` (`io/excel_tester.py`), поэтому
  wakeup-входы попадут автоматически.
- Старый volume-фильтр (`VolumeRuntime`) — отдельная подсистема;
  wakeup volume к ней не относится и ею не блокируется.
- `ZigZagGlobalStats` — `frozen=True`; новые поля — только с дефолтами.

---

## 5. Config-модель

### 5.1 Целевой YAML

```yaml
trade_filter:
  enabled: true
  type: zigzag_st_mode

  zigzag:
    enabled: true
    mode: D
    reversal_threshold: 0.005
    candidate_trigger_threshold: 0.012   # обязателен numeric (см. 5.2)
    local_window: 5
    global_stats_source: full_dataset

  lifecycle:
    exit_off_mode: "exit C"

  wakeup_regime:
    enabled: true

    entry:
      candidate_height:
        enabled: true
        quantile: 0.65
      candidate_age:
        enabled: true
        max_bars: 10
      atr_expansion:
        enabled: true
        short_window: 5
        long_window: 60
        min_ratio: 1.3
      volume_expansion:
        enabled: true
        short_window: 5
        baseline_window: 60
        min_ratio: 1.3

    exit:
      ttl:
        enabled: true
        bars: 45
      no_fresh_candidate:
        enabled: true
        quantile: 0.60
        max_age_bars: 15
        timeout_bars: 20
      action:
        mode: block_new_entries   # block_new_entries | close_position
```

`lifecycle.exit_off_mode: "exit C"` означает **набор условий выхода**
(TTL / no_fresh_candidate). **Что делать при срабатывании** задаёт
`wakeup_regime.exit.action.mode`.

`structural_compression` в Phase 0 отсутствует (см. §24).

### 5.2 Обязательные ограничения Phase 0

`candidate_trigger_threshold` нужен существующему ZigZag-пайплайну,
но Mode D его для входа не использует. Чтобы исключить скрытые legacy
quantile-сбои:

```text
Mode D требует candidate_trigger_threshold numeric.
Mode D отвергает candidate_trigger_threshold: "auto".
Mode D отвергает candidate_trigger_quantile.
```

### 5.3 Dataclasses (в `trade_filter_config.py`)

```text
TradeFilterWakeupRegimeConfig(enabled, entry, exit)
TradeFilterWakeupEntryConfig(candidate_height, candidate_age,
                             atr_expansion, volume_expansion)
TradeFilterWakeupCandidateHeightConfig(enabled, quantile)
TradeFilterWakeupCandidateAgeConfig(enabled, max_bars)
TradeFilterWakeupAtrExpansionConfig(enabled, short_window,
                                    long_window, min_ratio)
TradeFilterWakeupVolumeExpansionConfig(enabled, short_window,
                                       baseline_window, min_ratio)
TradeFilterWakeupExitConfig(ttl, no_fresh_candidate, action)
TradeFilterWakeupTtlExitConfig(enabled, bars)
TradeFilterWakeupNoFreshCandidateExitConfig(enabled, quantile,
                                            max_age_bars, timeout_bars)
TradeFilterWakeupExitActionConfig(mode)
```

Все числовые поля по умолчанию `None` (дефолты не кодируют стратегию).
Значения хранятся «как из YAML» (без коэрции), чтобы валидатор сообщал
точный тип-mismatch.

В `TradeFilterConfig` добавить:

```python
wakeup_regime: Optional[TradeFilterWakeupRegimeConfig] = None
```

Расширить:

- `_VALID_ZIGZAG_MODES` значением `"D"`;
- литералы `exit_off_mode` значением `"exit C"`;
- `build_trade_filter_config_from_raw` (парсинг вложенного `wakeup_regime`);
- `__all__`.

### 5.4 Whitelist путей (полный перечень)

Без регистрации каждого mapping-пути рекурсивный коллектор
`collect_trade_filter_unknown_keys` будет либо отвергать валидные ключи,
либо пропускать опечатки. Добавить `wakeup_regime` в набор корня
`trade_filter` и зарегистрировать все вложенные пути:

```text
trade_filter.wakeup_regime
  -> {enabled, entry, exit}

trade_filter.wakeup_regime.entry
  -> {candidate_height, candidate_age, atr_expansion, volume_expansion}

trade_filter.wakeup_regime.entry.candidate_height
  -> {enabled, quantile}

trade_filter.wakeup_regime.entry.candidate_age
  -> {enabled, max_bars}

trade_filter.wakeup_regime.entry.atr_expansion
  -> {enabled, short_window, long_window, min_ratio}

trade_filter.wakeup_regime.entry.volume_expansion
  -> {enabled, short_window, baseline_window, min_ratio}

trade_filter.wakeup_regime.exit
  -> {ttl, no_fresh_candidate, action}

trade_filter.wakeup_regime.exit.ttl
  -> {enabled, bars}

trade_filter.wakeup_regime.exit.no_fresh_candidate
  -> {enabled, quantile, max_age_bars, timeout_bars}

trade_filter.wakeup_regime.exit.action
  -> {mode}
```

---

## 6. Валидация конфига

Добавить:

```python
_validate_wakeup_regime_block(
    tf, errors, raw_user_keys, caller_pipeline, error_keys
)
```

**Точка подключения:** внутри `_validate_subfilter_legality_dispatch`,
в zigzag-enabled ветке, после `_validate_zigzag_block` /
`_validate_lifecycle_block`. `caller_pipeline` пробросить в этот блок
из `validate_trade_filter` (сейчас он идёт только в финальный gate).

### 6.1 Caller gate

```text
caller_pipeline == "tester":
  Mode D + exit C + wakeup_regime разрешены

caller_pipeline == "wf_grid":
  zigzag.mode == "D"                  -> ConfigError
  lifecycle.exit_off_mode == "exit C" -> ConfigError
  trade_filter.wakeup_regime present  -> ConfigError
```

Реджект для wf_grid держится именно здесь: wakeup-ключи теперь
whitelisted на уровне схемы (§5.4), поэтому блок должен исполняться
на enabled-пути.

### 6.2 Cross-field guards

```text
zigzag.mode == "D" требует lifecycle.exit_off_mode == "exit C"
zigzag.mode == "D" требует наличия raw-ключа lifecycle.exit_off_mode
zigzag.mode == "D" требует wakeup_regime.enabled == true
zigzag.mode == "D" отвергает candidate_trigger_threshold == "auto"
zigzag.mode == "D" отвергает candidate_trigger_quantile

lifecycle.exit_off_mode == "exit C" требует zigzag.mode == "D"
exit C отвергает exit_off_zz_leg_count
exit C отвергает exit_b_immediate_off
exit C отвергает legacy triggers block

zigzag.mode != "D" отвергает wakeup_regime, если он присутствует
```

A/B/C/A+B/C+B остаются валидными с Exit A/B как сейчас.

### 6.3 Field validation

```text
enabled: bool
quantile: finite float, 0 < q < 1
bars, max_bars, max_age_bars, timeout_bars,
short_window, long_window, baseline_window: int >= 1
min_ratio: finite float > 0
atr_expansion.long_window >= atr_expansion.short_window
volume_expansion.baseline_window >= volume_expansion.short_window
exit.action.mode: ровно один из {"block_new_entries", "close_position"}
exit.action.mode обязателен и валиден, когда wakeup_regime.enabled == true
любое иное значение -> ConfigError
```

Правила компонентов:

```text
enabled компонент: все обязательные поля присутствуют и валидны
disabled компонент: runtime трактует как passed (entry) / never fires (exit);
                    валидатор разрешает отсутствие числовых полей
```

---

## 7. Global stats

Расширить `ZigZagGlobalStats` (frozen, поля с дефолтами):

```python
wakeup_entry_candidate_height_threshold: Optional[float] = None
wakeup_no_fresh_candidate_height_threshold: Optional[float] = None
```

В `build_zigzag_global_stats` вычислять только для Mode D и только
если соответствующий компонент enabled:

```text
wakeup_entry_candidate_height_threshold =
  quantile(confirmed_heights_pct, entry.candidate_height.quantile)

wakeup_no_fresh_candidate_height_threshold =
  quantile(confirmed_heights_pct, exit.no_fresh_candidate.quantile)
```

Используется `np.quantile(..., method="linear")` — как в существующей
auto-ветке, для версионной стабильности.

**Min-leg guard** (не конфликтует с существующей auto-веткой):

```text
если Mode D и хотя бы один enabled wakeup-компонент требует quantile:
  required_legs = max(zigzag.local_window, 10)
  если n_legs_total < required_legs -> ConfigError
```

Для Mode D `candidate_trigger_threshold` материализуется через
существующую explicit-ветку (numeric) и не навязывает auto-quantile сбоев.

---

## 8. Data plumbing для high/low/volume (явно)

**Факт:** `run_backtest_fast` сейчас принимает OHLC
(`open/high/low/close`), но **не** `volume` как сырой массив.
`volume` попадает в систему только как предвычисленный `VolumeRuntime`.
Поэтому добавляем `volume` как **новый трейлинговый `Optional`
параметр** по цепочке tester:

```text
run_all_periods   -> не менять (передаёт df_slice, в нём есть колонка volume)
run_period        -> читает df["volume"] (если есть) и форвардит дальше
run_single_backtest -> + volume: Optional[np.ndarray] = None  (трейлинг)
run_backtest_fast   -> + volume: Optional[np.ndarray] = None  (трейлинг)
apply               -> + high, low, volume: Optional[np.ndarray] = None (трейлинг)
```

`high`/`low` в `run_backtest_fast`/`run_single_backtest` уже есть —
их просто пробросить в `apply`.

**Правила:**

```text
high/low требуются только при Mode D и atr_expansion.enabled == true
volume требуется только при Mode D и volume_expansion.enabled == true
старые вызовы без volume работают (volume_expansion отключён или не Mode D)
```

**Совместимость:**
Трейлинговый `Optional=None` не ломает ~20+ call-sites
`run_backtest_fast` в `walk_forward.py` / оптимизаторе.

При `early_exit` (в tester `early_exit=False`, но контракт полный):
если `volume` передан — усекать синхронно с остальными массивами.

**Валидация входов внутри Mode D setup:**

```text
длины high, low, close совпадают
длина volume совпадает с close, если volume передан
high/low финитны и > 0, если ATR-компонент enabled
volume финитен и >= 0, если volume-компонент enabled
```

---

## 9. Runtime-метрики wakeup

Чистые хелперы в `zigzag_st_filter.py`:

```text
_compute_wakeup_atr_ratio(high, low, close, short_window, long_window) -> np.ndarray
_compute_wakeup_volume_ratio(volume, short_window, baseline_window) -> np.ndarray
```

**ATR:**

- использовать существующие `calculate_true_range` и `calculate_atr_rma`
  (`core/calculator.py`);
- `atr_ok = false` для баров `< long_window - 1`;
- если `n < long_window` — вернуть массив NaN, без необработанных исключений;
- нефинитный ratio → `atr_ok=false`.

**Volume:**

- независим от `trade_filter.volume` / `VolumeRuntime`;
- rolling mean в Phase 0;
- warmup без baseline → `volume_ok=false`;
- baseline `<= 0` → `volume_ok=false`;
- нефинитный ratio → `volume_ok=false`.

Disabled entry-компонент всегда passed, даже если его массивы отсутствуют.

---

## 10. Mode D Entry

Вход оценивается только из `OFF`. Условие — AND включённых компонентов:

```text
candidate_height_ok:
  disabled -> true
  enabled  -> finite candidate_height_pct >= wakeup_entry_candidate_height_threshold

candidate_age_ok:
  disabled -> true
  enabled  -> candidate_age_bars > 0 AND candidate_age_bars <= max_bars
  (предикат > 0, как в legacy duration-gate; -1 = UNKNOWN не проходит)

candidate_direction_ok:
  candidate_leg_direction in (+1, -1)

trade_mode_ok:
  _trade_mode_allows_direction(candidate_leg_direction, trade_mode)

atr_ok:
  disabled -> true
  enabled  -> finite atr_ratio >= min_ratio

volume_ok:
  disabled -> true
  enabled  -> finite volume_ratio >= min_ratio
```

Дополнительные gate:

```text
combined_reset_event[t] блокирует вход
time_filter_in_window[t] == false блокирует вход
состояние должно быть OFF в начале бара
legacy trade_filter.volume gate не блокирует Mode D
legacy candidate_trigger_threshold не блокирует Mode D
```

При входе на close(t):

```text
state = ST_ACTIVE_FREEZE
held_pos = candidate_leg_direction
wakeup_cycle_age_bars = 0
wakeup_bars_since_fresh_candidate = 0 если fresh_candidate_ok, иначе 1
wakeup_regime_active = 1
trade_filter_trigger_source = "wakeup_regime"
```

Позиция open-to-open: решение на close(t), позиция активна на open(t+1).

---

## 11. FSM-состояния Mode D

```text
block_new_entries использует: OFF, ST_ACTIVE_FREEZE, ST_STOPPING
close_position    использует: OFF, ST_ACTIVE_FREEZE
                  (ST_STOPPING не задействуется)
```

Семантика:

```text
OFF:
  окно закрыто, позиции нет, счётчики = -1

ST_ACTIVE_FREEZE:
  окно открыто; позиция в candidate-направлении;
  same-direction ST flip — без эффекта;
  opposite ST flip — закрытие позиции и переход в OFF

ST_STOPPING  (только block_new_entries):
  новые входы запрещены; текущая позиция удерживается;
  реверсы запрещены; opposite ST flip закрывает и переводит в OFF
```

Внутренних разворотов нет ни в одном режиме.

---

## 12. Счётчики wakeup

**`wakeup_cycle_age_bars`:**

```text
trigger bar: 0
далее +1 каждый бар в ST_ACTIVE_FREEZE и ST_STOPPING (block_new_entries)
close_position: сбрасываются в -1 на баре закрытия Exit C
OFF/reset: -1
```

**`wakeup_bars_since_fresh_candidate`:**

```text
trigger bar:
  0 если fresh_candidate_ok на trigger-баре
  1 иначе
каждый следующий бар окна:
  если fresh_candidate_ok: 0
  иначе: предыдущее + 1
block_new_entries: растёт в ST_STOPPING
close_position: сбрасывается в -1 на баре закрытия
OFF/reset: -1
```

Правило фиксировано: на одном trigger-сценарии не допускается
одновременно 0 и 1 (граничные тесты обязательны).

---

## 13. Exit C

`lifecycle.exit_off_mode: "exit C"` — это набор **условий** выхода
окна. Условия — OR с приоритетом: `ttl` > `no_fresh_candidate`.

### 13.1 TTL

```text
ttl_triggered = ttl.enabled AND wakeup_cycle_age_bars >= ttl.bars
```

### 13.2 No Fresh Candidate

Fresh candidate:

```text
candidate_height_pct >= wakeup_no_fresh_candidate_height_threshold
AND candidate_age_bars > 0
AND candidate_age_bars <= no_fresh_candidate.max_age_bars
```

Недоступные данные кандидата → `fresh_candidate_ok=false`.

Триггер:

```text
no_fresh_candidate_triggered =
  no_fresh_candidate.enabled
  AND wakeup_bars_since_fresh_candidate >= timeout_bars
```

### 13.3 Единый приоритет завершения окна на баре

На одном баре события разрешаются строго по приоритету:

```text
1. reset (combined_reset_event[t])  -> §14
2. opposite_st_flip                  -> §15
3. Exit C (ttl, затем no_fresh)      -> §13.4
```

Если сработал более высокий приоритет — нижестоящие на этом баре
не эмитятся (single reason на бар).

### 13.4 Действие Exit C по `action.mode`

Trigger-массивы Exit C (`wakeup_exit_ttl_triggered` /
`wakeup_exit_no_fresh_candidate_triggered`) и `wakeup_exit_reason`
пишутся **только на баре первого срабатывания** из `ST_ACTIVE_FREEZE`.

**`action.mode == block_new_entries`:**

```text
если позиция есть:
  state = ST_STOPPING
  wakeup_exit_reason = выбранная причина (ttl | no_fresh_candidate)
  позицию НЕ закрывать; держать до opposite_st_flip/reset

если позиции нет:
  state = OFF
  счётчики = -1
  wakeup_exit_reason записан

в ST_STOPPING счётчики растут, trigger-массивы повторно НЕ эмитятся
```

**`action.mode == close_position`:**

```text
если позиция есть:
  filtered_positions[t+1] = 0  (закрытие по open(t+1))
  state = OFF
  wakeup_exit_reason = выбранная причина (ttl | no_fresh_candidate)
  wakeup_exit_close_triggered[t] = 1
  счётчики -> -1; wakeup_regime_active далее = 0

если позиции нет:
  state = OFF
  счётчики = -1
  wakeup_exit_reason записан

ST_STOPPING не задействуется; opposite_st_flip не ожидается
после OFF новое окно может открыться на следующем квалифицирующем баре
```

---

## 14. Reset и Time Filter

`combined_reset_event` включает daily reset и time-filter reset.

На reset-баре (приоритет 1):

```text
если окно было активно: wakeup_exit_reason = "reset"
state = OFF
held_pos = 0
wakeup_regime_active = 0
wakeup_cycle_age_bars = -1
wakeup_bars_since_fresh_candidate = -1
```

Reset-бар не может открыть новое окно. Вне `time_filter_in_window`
новое окно не открывается. Reset-семантика берётся из уже вычисленного
time-filter reset-события.

---

## 15. Opposite ST Flip (приоритет 2)

```text
same-direction ST flip: без эффекта (в обоих режимах)

opposite ST flip в ST_ACTIVE_FREEZE (оба режима):
  filtered_positions[t+1] = 0
  state = OFF
  wakeup_exit_reason = "opposite_st_flip"
  счётчики = -1

opposite ST flip в ST_STOPPING (только block_new_entries):
  filtered_positions[t+1] = 0
  state = OFF
  wakeup_exit_reason = "opposite_st_flip"
  счётчики = -1
```

Разворотная позиция не открывается.

Примечание: при `close_position` к моменту, когда Exit C сработал,
окно уже в OFF, поэтому ветка `ST_STOPPING + opposite flip` недостижима.

---

## 16. Контракт диагностики

Mode D эмитит полный dict, совместимый с tester summary и Excel.
Все массивы длины `n`; dtypes совпадают с контрактом.

### 16.1 Общие ключи (тот же dtype-семейство, что у legacy)

```text
trade_filter_state                          object
trade_filter_state_code                     int64
trade_filter_trigger_source                 object
confirmed_legs_since_start                  int64
st_flip_dir                                 int8
trade_filter_enabled                        int8
zigzag_reversal_threshold                   float64
candidate_height_pct                        float64
candidate_trigger_threshold                 float64
local_median_N                              float64
local_median_available                      int8
local_window                                int64
global_median                               float64
global_stats_available                      int8
freeze_confirmed_legs                       int64  (sentinel -1 для Mode D)
median_stop_triggered                       int8   (всегда 0 для Mode D)
stopping_started_at_index                   int64
filter_allowed_entry                        int8
filter_block_reason                         object
exit_off_mode                               object ("exit C")
exit_off_zz_leg_count                       int64  (-1)
zz_legs_since_lifecycle_start               int64  (-1)
zz_leg_stop_triggered                       int8   (0)
exit_b_immediate_off_triggered              int8   (0)
exit_b_immediate_off_config                 int8   (0)
daily_reset_enabled                         int8
daily_reset_event                           int8
time_filter_enabled                         int8
time_filter_in_window                       int8
time_filter_reset_event                     int8
zigzag_mode                                 object ("D")
candidate_age_bars                          int64
candidate_leg_direction                     int8
candidate_duration_gate_enabled             int8   (0)
candidate_duration_max_bars                 int64  (-1)
candidate_duration_gate_passed              int8   (1)
candidate_threshold_ok                      int8   (sentinel 0)
candidate_component_ok                      int8   (wakeup candidate-height ok)
confirmed_median_ok                         int8   (0)
b_component_ok                              int8   (0)
immediate_allowed                           int8   (wakeup direction/trade_mode gate)
immediate_candidate_entry_used              int8   (0 для Mode D)
immediate_candidate_entry_block_reason      object (Mode D-specific sentinel)
```

### 16.2 Wakeup-ключи

```text
wakeup_regime_active                        int8
wakeup_entry_all_ok                         int8
wakeup_entry_candidate_height_ok            int8
wakeup_entry_candidate_age_ok               int8
wakeup_entry_candidate_direction_ok         int8
wakeup_entry_atr_ok                         int8
wakeup_entry_volume_ok                      int8
wakeup_entry_candidate_height_value         float64
wakeup_entry_candidate_height_threshold     float64
wakeup_entry_candidate_age_bars             int64
wakeup_entry_candidate_leg_direction        int8
wakeup_entry_atr_ratio                      float64
wakeup_entry_volume_ratio                   float64
wakeup_cycle_age_bars                       int64
wakeup_bars_since_fresh_candidate           int64
wakeup_exit_ttl_triggered                   int8
wakeup_exit_no_fresh_candidate_triggered    int8
wakeup_exit_close_triggered                 int8   (1 только при close_position)
wakeup_exit_action_mode                     object ("block_new_entries" | "close_position", константа-эхо)
wakeup_exit_reason                          object
```

`wakeup_exit_reason` значения:
`none`, `ttl`, `no_fresh_candidate`, `reset`, `opposite_st_flip`.

Режим выхода восстанавливается из пары
`wakeup_exit_action_mode` + `wakeup_exit_close_triggered`.

---

## 17. Summary и Excel

### 17.1 Tester summary

`_build_filter_diagnostics_summary` — сделать mode-aware
(не предполагать legacy-only ключи). Для Mode D добавить:

```text
wakeup_starts_count                   = sum(trigger_source == "wakeup_regime")
wakeup_exit_ttl_count                 = sum(wakeup_exit_ttl_triggered)
wakeup_exit_no_fresh_candidate_count  = sum(wakeup_exit_no_fresh_candidate_triggered)
wakeup_exit_close_count               = sum(wakeup_exit_close_triggered)
wakeup_exit_reset_count               = sum(wakeup_exit_reason == "reset")
wakeup_exit_opposite_st_flip_count    = sum(wakeup_exit_reason == "opposite_st_flip")
wakeup_bars_active                    = sum(wakeup_regime_active)
```

Threshold echo:

```text
wakeup_entry_candidate_height_threshold
wakeup_no_fresh_candidate_height_threshold
wakeup_entry_candidate_height_quantile
wakeup_no_fresh_candidate_quantile
```

Config echo:

```text
zigzag_mode = "D"
exit_off_mode = "exit C"
wakeup_enabled = true
wakeup_exit_action_mode
wakeup_candidate_age_max_bars
wakeup_atr_short_window / wakeup_atr_long_window / wakeup_atr_min_ratio
wakeup_volume_short_window / wakeup_volume_baseline_window / wakeup_volume_min_ratio
wakeup_ttl_bars
wakeup_no_fresh_max_age_bars / wakeup_no_fresh_timeout_bars
```

### 17.2 Excel

- Добавить wakeup-поля в `FILTER_DIAGNOSTICS_100_DISPLAY_NAMES`
  (включая `wakeup_exit_action_mode` и `wakeup_exit_close_triggered`).
- Добавить wakeup summary-поля в `filters_summary`.
- `ZigZag_Trigger_Events` — wakeup-входы попадают автоматически
  (`trigger_source != "none"`); «Triggered Lifecycle Start» = true
  (состояние на trigger-баре = `ST_ACTIVE_FREEZE`). Дополнительной
  логики не требуется — нужны только display-names.

---

## 18. Signal events и trade diagnostics

Перед реализацией прочитать `attach_trade_filter_diagnostics`
(`zigzag_st_filter.py`) и `signal_events.build_signal_events`
и зафиксировать поведение:

```text
ZigZag_Trigger_Events показывает wakeup trigger-бары (§17.2).
FilterDiagnostics_100 показывает wakeup trigger-бары.
signal_events НЕ падает при trigger_source == "wakeup_regime".
entry_trigger_source подхватывает "wakeup_regime"
  через trade_filter_trigger_source.
trade-level exit_reason: opposite-flip выходы Mode D маппятся на
  существующий "filter_stopping_opposite_flip" ИЛИ документированное
  wakeup-значение — с тестом.
```

**Обязателен интеграционный тест** полного прогона Mode D через экспорт:
без падений, непустой `ZigZag_Trigger_Events`,
корректная линковка trade↔trigger. Тест прогоняется для
обоих `action.mode`.

---

## 19. Тесты

### 19.1 Config и caller gate

```text
tester принимает D + exit C + wakeup_regime.enabled=true
tester отвергает D без raw exit_off_mode="exit C"
tester отвергает D без wakeup_regime.enabled=true
tester отвергает exit C при mode != D
tester отвергает exit C + exit_off_zz_leg_count
tester отвергает exit C + exit_b_immediate_off
tester отвергает D + candidate_trigger_threshold="auto"
tester отвергает D + candidate_trigger_quantile
wf_grid отвергает Mode D
wf_grid отвергает exit C
wf_grid отвергает wakeup_regime
старые modes/exits по-прежнему принимаются
каждый валидный wakeup-ключ принимается;
неизвестный ключ в каждом под-блоке отвергается
```

### 19.2 Wakeup config fields

```text
enabled не-bool отвергается
quantile вне (0,1) отвергается
windows/bars < 1 отвергается
min_ratio <= 0 отвергается
atr long_window < short_window отвергается
volume baseline_window < short_window отвергается
action.mode == "block_new_entries" принимается
action.mode == "close_position" принимается
action.mode иное значение -> ConfigError
action.mode отсутствует при enabled wakeup -> ConfigError
disabled компоненты могут опускать числовые параметры
enabled компоненты требуют числовые параметры
```

### 19.3 Global stats

```text
entry-threshold считается из заданного quantile
no-fresh threshold считается из заданного quantile
disabled quantile-компонент не считает threshold
min-leg guard применяется только к enabled Mode D wakeup-quantile компонентам
старые modes не падают из-за wakeup quantile-проверок
Mode D numeric candidate_trigger_threshold по-прежнему материализуется
```

### 19.4 Runtime metrics

```text
ATR ratio использует calculate_true_range + RMA
n < long_window -> все atr_ok=false, без необработанного ValueError
ATR warmup блокирует до long_window - 1
ATR NaN/Inf -> atr_ok=false
volume warmup -> volume_ok=false
volume baseline zero -> volume_ok=false
volume NaN/Inf -> volume_ok=false
disabled entry-компонент всегда passed
disabled компонент без данных всё равно passed
```

### 19.5 Entry Mode D

```text
candidate_height гейтит вход
candidate_age гейтит вход (age > 0; -1 блокирует)
candidate_direction гейтит вход
trade_mode гейтит направление
ATR гейтит вход
volume гейтит вход
все enabled компоненты комбинируются как AND
candidate_direction == 0 блокирует
time_filter вне окна блокирует
combined_reset бар блокирует
legacy candidate_trigger_threshold не гейтит вход Mode D
legacy trade_filter.volume gate не блокирует Mode D
```

### 19.6 FSM и позиции

```text
Mode D входит сразу из OFF
Mode D никогда не входит в WAIT_FIRST_ST_FLIP
UP candidate -> long; DOWN candidate -> short
позиция появляется на t+1
same-direction ST flip удерживает (оба режима)
legacy FSM loop не исполняется для Mode D
нет ST_ACTIVE_MONITORING / ST_COUNTING_ZZ_LEGS для Mode D
```

### 19.7 Счётчики и Exit C

```text
# условия (общие для обоих action.mode)
trigger bar age = 0; next bar age = 1
wakeup_bars_since_fresh_candidate:
  0 если fresh на trigger-баре, иначе 1 (граничный тест обязателен)
TTL триггерит при age >= bars
no_fresh триггерит при bars_since_fresh >= timeout_bars
приоритет внутри Exit C: ttl > no_fresh_candidate
приоритет на баре: reset > opposite_st_flip > exit_c
trigger-массивы Exit C эмитятся только на баре первого срабатывания
disabled exit-компонент никогда не триггерит

# action.mode == block_new_entries
Exit C с позицией -> ST_STOPPING; позиция держится
ST_STOPPING запрещает входы и реверсы
ST_STOPPING держит до opposite_st_flip (затем OFF, reason=opposite_st_flip)
счётчики растут в ST_STOPPING

# action.mode == close_position
Exit C с позицией -> закрытие на t+1; state = OFF
wakeup_exit_close_triggered=1 на баре закрытия
счётчики сброшены в -1; wakeup_regime_active далее = 0
ST_STOPPING не достигается
opposite_st_flip не ожидается после закрытия
после OFF новое окно открывается на следующем квалифицирующем баре
обе причины (ttl, no_fresh_candidate) корректно закрывают позицию
```

### 19.8 Reset

```text
combined_reset закрывает активное окно
reset пишет wakeup_exit_reason="reset" только при закрытии активного окна
reset чистит счётчики в -1
reset бар не открывает новое окно
```

### 19.9 Opposite ST flip

```text
opposite flip в ST_ACTIVE_FREEZE -> закрытие, OFF (оба режима)
opposite flip в ST_STOPPING -> закрытие, OFF (только block_new_entries)
same-direction flip -> без эффекта
разворотная позиция не открывается
```

### 19.10 Diagnostics / Summary / Export

```text
Mode D диагностика включает полный общий keyset
Mode D диагностика включает все wakeup-поля (§16.2)
все массивы длины n; dtypes по контракту
wakeup_exit_action_mode присутствует и равен конфигу
wakeup_exit_close_triggered корректен
  (1 только на баре close_position-закрытия)
FilterDiagnostics_100 содержит wakeup display-names
ZigZag_Trigger_Events включает wakeup строки
filter_diagnostics_summary включает wakeup counters/thresholds/config echo
wakeup_starts_count == count(trigger_source == "wakeup_regime")
wakeup_bars_active == sum(wakeup_regime_active)
wakeup_exit_close_count == sum(wakeup_exit_close_triggered)
legacy modes сохраняют свой keyset и значения
полный прогон Mode D через экспорт не падает (оба action.mode);
trade↔trigger линковка корректна
```

### 19.11 Regression

```text
Modes A / B / C / A+B / C+B без изменений
exit A / exit B / exit_b_immediate_off без изменений
time_filter / daily_reset без изменений
standalone volume без изменений
zigzag + old volume gate без изменений
старые вызовы run_backtest_fast без volume работают
старые вызовы apply без high/low/volume работают
wf_grid reject-тесты проходят
```

---

## 20. Порядок реализации

> Ориентир: ~2–3 недели для одного инженера с тестами.
> Этапы 0a (1–11) дают вертикальный срез под критерий успеха;
> 0b (12–21) — полнота диагностики/экспорта.

```text
Phase 0a — вертикальный срез:
 1. Зафиксировать зелёный baseline на donor/ (явный PYTHONPATH).
 2. Config dataclasses + parser + whitelist-пути + __all__.
 3. Caller gate + полная wakeup-валидация
    (+ проброс caller_pipeline в _validate_wakeup_regime_block).
 4. Config-тесты (включая wf_grid reject и оба action.mode).
 5. Расширить ZigZagGlobalStats wakeup-порогами + min-leg guard.
 6. Тесты global stats.
 7. Data plumbing high/low/volume по всей цепочке (трейлинг Optional).
 8. Тесты ATR/volume хелперов.
 9. Аллокация общего Mode D-совместимого keyset диагностики.
10. Dispatch в apply() на якорь -> _run_wakeup_fsm (legacy loop не трогаем).
11. _evaluate_wakeup_entry + _run_wakeup_fsm (вход, open-to-open) + тесты.

Phase 0b — полнота:
12. Счётчики wakeup + граничные тесты.
13. Exit C — условия TTL и no_fresh (общая логика).
14. action.mode == block_new_entries (ST_STOPPING) + тесты.
15. action.mode == close_position (немедленное закрытие) + тесты.
16. Reset + opposite ST flip + тесты.
17. Summary mode-aware + тесты.
18. Excel display-names + filters_summary + тесты.
19. ZigZag_Trigger_Events / FilterDiagnostics_100 (проверка автоматики).
20. signal/trade diagnostics совместимость + интеграционный тест экспорта
    (оба action.mode).
21. Legacy regression-тесты + single-config tester verification.
```

---

## 21. Команды верификации

Все тесты запускаются с `PYTHONPATH=...\donor` (единственное рабочее
дерево). `donor TESTER/` не используется.

```text
python -m pytest wf_grid/tests/test_wp2_config_trade_filter.py -q
python -m pytest wf_grid/tests/test_wp3_zigzag_global_stats.py -q
python -m pytest wf_grid/tests/test_wp4_zigzag_per_bar.py -q
python -m pytest wf_grid/tests/test_wp5_zigzag_fsm.py -q
python -m pytest wf_grid/tests/test_wp7_backtest_integration.py -q
python -m pytest <новые donor/-тесты wakeup> -q
```

Single-config tester acceptance (прогнать оба варианта):

```text
# block_new_entries
zigzag.mode = D, exit_off_mode = exit C, action.mode = block_new_entries
-> wakeup_starts_count > 0
-> позиции по candidate_leg_direction, без WAIT_FIRST_ST_FLIP
-> Exit C переводит в ST_STOPPING, новые входы заблокированы
-> opposite ST flip закрывает позицию без разворота
-> экспорт не падает

# close_position
action.mode = close_position
-> Exit C закрывает позицию на t+1, state = OFF
-> wakeup_exit_close_triggered = 1 на баре закрытия
-> новое окно может открыться на следующем квалифицирующем баре
-> экспорт не падает

# общее
-> legacy modes/exits без изменений
-> wf_grid отвергает Phase 0 wakeup-конфиг
```

---

## 22. Главные риски и митигации

| Риск | Митигация |
|---|---|
| Mode D протекает в `wf_grid` | Caller gate в shared-валидаторе + wf_grid reject-тесты; блок исполняется на enabled-пути |
| Регресс legacy A/B/C из-за правок | Ранний dispatch на якорь; legacy loop и его массивы не трогаются |
| Недооценка volume-plumbing | Трейлинг Optional по всей цепочке; явный список call-sites; тесты old-caller |
| Путаница деревьев `donor/` vs `donor TESTER/` | Единственное дерево `donor/`; верификация только по нему |
| Summary падает на отсутствующих ключах | Mode D эмитит полный общий keyset с sentinels; summary mode-aware |
| `candidate_trigger_threshold: auto` | Реджект auto в Mode D |
| Wakeup volume конфликтует со старым volume | Отдельный хелпер; `VolumeRuntime` для Mode D игнорируется |
| ATR хелпер падает на коротких данных | Возврат all-NaN, fail-closed |
| Exit C двойной счёт в ST_STOPPING | Trigger-массивы только на баре перехода |
| Off-by-one в счётчиках | Граничные тесты trigger/next/TTL/no-fresh |
| Trade/signal экспорт падает на wakeup | Прочитать реальные функции; зафиксировать маппинг; интеграционный тест (оба action.mode) |
| close_position открывает позицию сразу после закрытия | Тест: подтвердить, что новый вход возможен только на следующем **квалифицирующем** баре |

---

## 23. Definition of Done

```text
- Mode D-конфиг принимается только в tester и отвергается wf_grid.
- Mode D входит сразу из OFF в candidate-направлении.
- Mode D не исполняет legacy FSM loop.
- Exit C поддерживает оба action.mode:
    block_new_entries: ST_STOPPING, удержание до opposite_st_flip;
    close_position: немедленное закрытие на t+1, OFF, сброс counters.
- Reset и time_filter совместимы с существующим поведением.
- Диагностика и summary различают режим выхода
  (wakeup_exit_action_mode + wakeup_exit_close_triggered).
- Тесты покрывают оба режима, приоритет бара и переоткрытие окна
  после close_position.
- Legacy modes и существующий volume проходят regression-тесты.
- Single-config прогон (оба action.mode) даёт осмысленные
  wakeup-counters без падений экспорта.
```

---

## 24. Deferred (вне Phase 0)

```text
structural_compression exit — отложен: на коротком wakeup-окне
  local_median_N часто недоступен (нужно >= local_window подтверждённых
  ног), поэтому компонент почти не триггерит и не оправдывает код/конфиг/
  тесты на стадии проверки гипотезы. Возвращать, если гипотеза
  подтвердится.

WF Grid runtime / export для Mode D.
WF OOS, parallel transport.
force_flat (глобальный flatten) — запрещён.
hard-close с немедленным разворотом / внутренние реверсы.
```
