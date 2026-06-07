# Wakeup Regime Phase 0 — Implementation Plan v3

## 1. Цель

Реализовать прототип `wakeup_regime` для donor-тестера и проверить гипотезу:

```text
свежий импульс цены + ATR-расширение + объём-расширение
→ немедленный вход в направлении candidate leg
→ ограниченное wakeup-окно
→ закрытие окна, когда импульс состарился или структура сжалась
```

Phase 0 — только donor-тестер. WF Grid, WF export, parallel runtime, WF OOS — вне скоупа.

---

## 2. Главный архитектурный принцип: изоляция Mode D

Mode D **не модифицирует существующий FSM-цикл** в `apply()`.

Схема:

```text
apply()
  │
  ├── pre-loop: per_bar, global_stats, combined_reset, time_filter
  │
  ├── resolved_mode == "D"  ──►  _run_wakeup_fsm(...)   ← новый, изолированный
  │                                    └── возвращает ZigZagSTFilterResult
  │
  └── иначе  ──►  существующий легаси for-loop  ← НЕ ТРОНУТ, ни одной строки
```

Легаси-цикл (WAIT / MONITORING / median-stop / exit B / reversal-update / legacy-volume-gate)
физически **не исполняется** для Mode D — не обходится условиями, а просто не запускается.

Это устраняет целый класс рисков (регрессии A/B/C/A+B/C+B) по построению.

`_run_wakeup_fsm` размещается в том же `zigzag_st_filter.py` — бесплатный доступ к
`ZigZagPerBar`, `ZigZagGlobalStats`, `detect_st_flip`, `_trade_mode_allows_direction`,
`ZigZagFSMState`, без кросс-импортов.

Общие примитивы можно выносить в маленькие helper'ы только если это реально снижает риск.
Phase 0 не требует большого рефактора легаси-цикла. Приоритет:

```text
1. Легаси for-loop не трогать.
2. _run_wakeup_fsm может локально повторить простую обвязку цикла.
3. Общий helper допустим только для коротких инвариантов без изменения легаси-семантики:
   - open-to-open запись позиции t+1;
   - reset-wipe значений;
   - сборка общего diagnostics subset.
```

Если вынос helper'а заставляет менять существующий легаси for-loop больше чем минимально,
helper откладывается, а поведение закрепляется тестами.

---

## 3. Принцип управляемости из config

Каждый компонент wakeup-режима управляется из config:

- `wakeup_regime.enabled` — включает/выключает весь режим.
- Каждый entry-компонент (`candidate_height`, `candidate_age`, `atr_expansion`,
  `volume_expansion`) имеет `enabled` и настраиваемые параметры.
- Каждый exit-компонент (`ttl`, `no_fresh_candidate`, `structural_compression`) имеет
  `enabled` и настраиваемые параметры.
- Все числовые параметры (квантили, длины окон, пороги, множители) задаются в config,
  никакие значения не захардкожены в коде.

Disabled entry-компонент считается **passed** на входе.
Disabled exit-компонент **не может** сработать.

---

## 4. Scope

Разрешено:

```text
donor/supertrend_optimizer/...
donor-тестерные тесты
точечные тесты shared-схемы (trade_filter_config.py, global stats)
```

Запрещено:

```text
wf_grid runtime / export / parallel transport / WF OOS
effective-config source tracking
производственный wakeup-runtime
```

---

## 5. Baseline

До начала реализации:

1. Зафиксировать текущий набор зелёных тестов.
2. Убедиться, что все легаси golden-тесты (Mode A/B/C/A+B/C+B, exit A/B) проходят.
3. Сохранить снэпшот как regression-baseline.

---

## 6. Целевой config

```yaml
trade_filter:
  enabled: true
  type: zigzag_st_mode

  zigzag:
    enabled: true
    mode: D
    reversal_threshold: 0.005
    candidate_trigger_threshold: 0.012   # обязателен для ZigZag-pipeline, Mode D не использует для входа
    local_window: 5
    global_stats_source: full_dataset

  lifecycle:
    exit_off_mode: "exit C"

  wakeup_regime:
    enabled: true

    entry:
      candidate_height:
        enabled: true
        quantile: 0.65           # вычисляется из confirmed_heights_pct, можно менять

      candidate_age:
        enabled: true
        max_bars: 10             # можно менять

      atr_expansion:
        enabled: true
        short_window: 5          # можно менять
        long_window: 60          # можно менять
        min_ratio: 1.3           # можно менять

      volume_expansion:
        enabled: true
        short_window: 5          # можно менять
        baseline_window: 60      # можно менять
        min_ratio: 1.3           # можно менять

    exit:
      ttl:
        enabled: true
        bars: 45                 # можно менять

      no_fresh_candidate:
        enabled: true
        quantile: 0.60           # вычисляется из confirmed_heights_pct, можно менять
        max_age_bars: 15         # можно менять
        timeout_bars: 20         # можно менять

      structural_compression:
        enabled: true
        min_cycle_age_bars: 15   # можно менять
        min_confirmed_legs: 2    # можно менять
        local_window: null       # null → берётся из trade_filter.zigzag.local_window
        global_median_multiplier: 0.9   # можно менять

      action:
        mode: block_new_entries  # единственный поддерживаемый в Phase 0
```

---

## 7. Config Schema

Файл: `donor/supertrend_optimizer/core/trade_filter_config.py`

### 7.1 Расширить существующее

```text
_VALID_ZIGZAG_MODES  += "D"
exit_off_mode литерал += "exit C"       (только для tester; gate в §8)
```

### 7.2 Новые dataclass'ы

```text
TradeFilterWakeupRegimeConfig
TradeFilterWakeupEntryConfig
TradeFilterWakeupCandidateHeightConfig     (enabled, quantile)
TradeFilterWakeupCandidateAgeConfig        (enabled, max_bars)
TradeFilterWakeupAtrExpansionConfig        (enabled, short_window, long_window, min_ratio)
TradeFilterWakeupVolumeExpansionConfig     (enabled, short_window, baseline_window, min_ratio)
TradeFilterWakeupExitConfig
TradeFilterWakeupTtlExitConfig             (enabled, bars)
TradeFilterWakeupNoFreshCandidateExitConfig (enabled, quantile, max_age_bars, timeout_bars)
TradeFilterWakeupStructuralCompressionExitConfig
                                           (enabled, min_cycle_age_bars, min_confirmed_legs,
                                            local_window, global_median_multiplier)
TradeFilterWakeupExitActionConfig          (mode: str = "block_new_entries")
```

Добавить поле в `TradeFilterConfig`:

```text
wakeup_regime: Optional[TradeFilterWakeupRegimeConfig] = None
```

### 7.3 Расширить `build_trade_filter_config_from_raw`

Парсить `wakeup_regime`-ветку из raw-dict в структуры выше.
Сохранять значения as-is (без coercion) — как уже сделано для всех остальных блоков.

### 7.4 Расширить `TRADE_FILTER_ALLOWED_KEYS`

Добавить все ключи под:

```text
trade_filter.wakeup_regime
trade_filter.wakeup_regime.entry
trade_filter.wakeup_regime.entry.candidate_height
trade_filter.wakeup_regime.entry.candidate_age
trade_filter.wakeup_regime.entry.atr_expansion
trade_filter.wakeup_regime.entry.volume_expansion
trade_filter.wakeup_regime.exit
trade_filter.wakeup_regime.exit.ttl
trade_filter.wakeup_regime.exit.no_fresh_candidate
trade_filter.wakeup_regime.exit.structural_compression
trade_filter.wakeup_regime.exit.action
```

Расширить `__all__` всеми новыми публичными именами.

---

## 8. Config Validation

Добавить `_validate_wakeup_regime_block(tf, errors, raw_user_keys, caller_pipeline)`.

### 8.1 Caller gate

```text
caller_pipeline == "tester":
  Mode D + exit C + wakeup_regime допустимы

caller_pipeline == "wf_grid":
  Mode D      → ConfigError
  exit C      → ConfigError
  wakeup_regime → ConfigError
```

Существующая инфраструктура (`cli/tester.py` → `caller_pipeline="tester"`,
`wf_grid/config/loader.py` → `caller_pipeline="wf_grid"`) используется без изменений.

### 8.2 Cross-field guards

```text
zigzag.mode == "D"  требует  lifecycle.exit_off_mode == "exit C"
zigzag.mode == "D"  требует  wakeup_regime.enabled == true
lifecycle.exit_off_mode == "exit C"  требует  zigzag.mode == "D"
exit C + exit_off_zz_leg_count      → ConfigError
exit C + exit_b_immediate_off       → ConfigError
```

Mode D по-прежнему требует `reversal_threshold`, `candidate_trigger_threshold`,
`local_window` (они нужны ZigZag-pipeline; Mode D не использует `candidate_trigger_threshold`
для логики входа).

### 8.3 Валидация полей

```text
enabled: bool
quantile: finite float, 0 < q < 1
bars / max_bars / max_age_bars / timeout_bars
  / min_cycle_age_bars / short_window / long_window
  / baseline_window: int >= 1
min_confirmed_legs: int >= 0
min_ratio / global_median_multiplier: finite float > 0
volume_expansion.baseline_window >= volume_expansion.short_window
structural_compression.local_window: null или int >= 1
exit.action.mode: строго "block_new_entries"
```

### 8.4 Семантика disabled-компонентов

```text
entry-компонент disabled  → runtime считает условие passed
exit-компонент  disabled  → runtime никогда не триггерит
```

force_flat не является ключом схемы → автоматически отвергается whitelist'ом
как unknown key; специального гарда не требуется.

---

## 9. Global Stats

Файл: `donor/supertrend_optimizer/core/zigzag_st_filter.py`

Расширить `ZigZagGlobalStats` двумя полями с дефолтом `None`:

```text
wakeup_entry_candidate_height_threshold: Optional[float] = None
wakeup_no_fresh_candidate_height_threshold: Optional[float] = None
```

Вычислять в `build_zigzag_global_stats` только если:

```text
zigzag.mode == "D"
и соответствующий wakeup-компонент enabled == true
```

Формула (config-driven, без хардкода):

```text
wakeup_entry_candidate_height_threshold =
  quantile(confirmed_heights_pct,
           wakeup_regime.entry.candidate_height.quantile)

wakeup_no_fresh_candidate_height_threshold =
  quantile(confirmed_heights_pct,
           wakeup_regime.exit.no_fresh_candidate.quantile)
```

Min-legs guard (только если хотя бы один enabled wakeup-компонент требует квантиль):

```text
min_legs_for_wakeup_quantile = max(zigzag.local_window, 10)
если n_legs_total < min_legs_for_wakeup_quantile → ConfigError
```

Легаси-режимы (A/B/C/A+B/C+B) эту проверку не проходят.

---

## 10. Runtime Data Plumbing

### 10.1 Полная цепочка

```text
run_period  →  run_single_backtest  →  run_backtest_fast  →  apply()
```

Все три сигнатуры расширяются backward-compatible:

```text
run_period(...)           добавить: volume: Optional[np.ndarray] = None
run_single_backtest(...)  добавить: volume: Optional[np.ndarray] = None
run_backtest_fast(...)    добавить: volume: Optional[np.ndarray] = None
apply(...)                добавить: high, low, volume: Optional[np.ndarray] = None
```

`high`/`low` уже присутствуют в `run_backtest_fast` и `run_single_backtest` — просто
прокидываются дальше в `apply()`.

### 10.2 Условная обязательность

```text
high, low  — обязательны только если mode==D и atr_expansion.enabled==true
volume     — обязателен только если mode==D и volume_expansion.enabled==true
старые вызовы без high/low/volume продолжают работать
```

### 10.3 Валидация на входе в apply()

```text
high/low/close длины совпадают
volume длина совпадает с close (если передан)
high/low: finite и > 0
volume: finite и >= 0
```

### 10.4 Семантика warmup ATR/volume

ATR и volume ratio — причинные ряды от бара 0 полного датасета.
`daily_reset` и `time_filter` wipe **не перезапускают** warmup.
`atr_ok = false` при `bar_index < long_window - 1`.
`volume_ok = false` в warmup-зоне baseline.

---

## 11. Runtime Metric Helpers

Предрасчитать массивы **до** FSM-цикла (не внутри):

```text
_compute_wakeup_atr_ratio(high, low, close, cfg) → np.ndarray  (atr_ratio per bar)
_compute_wakeup_volume_ratio(volume, cfg)         → np.ndarray  (volume_ratio per bar)
```

ATR:

```text
TR = calculate_true_range(high, low, close)          ← существующий helper
ATR_short = calculate_atr_rma(TR, short_window)      ← существующий helper
ATR_long  = calculate_atr_rma(TR, long_window)       ← существующий helper
atr_ratio = ATR_short / ATR_long
```

Volume:

```text
volume_short    = _rolling_aggregate(volume, short_window, "mean")   ← существующий helper
volume_baseline = _rolling_aggregate(volume, baseline_window, "mean")
volume_ratio    = volume_short / volume_baseline
```

Fail-closed правила:

```text
atr_ok = false   если bar_index < long_window - 1
atr_ok = false   если ratio NaN/Inf/non-finite
atr_ok = atr_ratio >= min_ratio  иначе

volume_ok = false  если baseline <= 0 или NaN/Inf
volume_ok = false  если ratio NaN/Inf/non-finite
volume_ok = volume_ratio >= min_ratio  иначе
```

---

## 12. Точка диспетча в apply()

В `apply()`, после материализации `per_bar` и `zigzag_global_stats`, до легаси-цикла:

```text
если resolved_mode == "D":
    предрасчитать atr_ratio_arr, volume_ratio_arr (если соотв. компоненты enabled)
    return _run_wakeup_fsm(
        per_bar, trend, combined_reset_event, time_filter_in_window,
        trade_filter_config, zigzag_global_stats,
        atr_ratio_arr, volume_ratio_arr,
        trade_mode, n
    )
```

Легаси-цикл (строки ~1871–2300 текущего `apply()`) для Mode D **не исполняется**.
`_try_lifecycle_start_from_off` для Mode D не вызывается; его `raise` на неизвестном
режиме не затрагивается.

---

## 13. FSM для Mode D: состояния

```text
OFF
ST_ACTIVE_FREEZE   ← wakeup-окно открыто, позиция удерживается
ST_STOPPING        ← Exit C сработал, позиция удерживается до opposite ST flip
```

`WAIT_FIRST_ST_FLIP`, `ST_ACTIVE_MONITORING`, `ST_COUNTING_ZZ_LEGS` — Mode D не использует.

---

## 14. Алгоритм `_run_wakeup_fsm` — per-bar порядок шагов

```text
для каждого бара t:

  ШАГ 0. Снапшот начала бара
    state_at_bar_start    = state
    held_pos_at_bar_start = held_pos
    (запись в диагностические массивы)

  ШАГ 1. Combined-reset wipe (если combined_reset_event[t])
    если wakeup-окно было открыто → wakeup_exit_reason = "reset"
    state = OFF
    held_pos = 0
    wakeup_regime_active = 0
    wakeup_cycle_age_bars = -1
    wakeup_bars_since_fresh_candidate = -1
    wakeup_confirmed_legs_since_start = -1

  ШАГ 2. Определить события бара
    cur_pos = filtered_positions[t]
    flip_dir = detect_st_flip(trend[t-1], trend[t])
    is_reset = combined_reset_event[t]
    fresh_candidate_ok = (§16 no_fresh_candidate условие)

  ШАГ 3. Инкремент wakeup_confirmed_legs_since_start
    только если:
      wakeup-окно уже было открыто на BAR START (state_at_bar_start != OFF)
      И confirm_event[t] == 1
      И NOT is_reset

  ШАГ 4. Инкремент wakeup_cycle_age_bars и wakeup_bars_since_fresh_candidate
    только если wakeup-окно было открыто на bar start И NOT is_reset:
      wakeup_cycle_age_bars += 1
      если fresh_candidate_ok: wakeup_bars_since_fresh_candidate = 0
      иначе:                    wakeup_bars_since_fresh_candidate += 1

  ШАГ 5. Оценить Exit C (только если state != OFF и NOT is_reset)
    Exit C не оценивается на trigger-баре (age на триггер-баре == 0, первая оценка при age >= 1)
    Условия OR с приоритетом:
      1. ttl:                   wakeup_cycle_age_bars >= ttl.bars
      2. no_fresh_candidate:    wakeup_bars_since_fresh_candidate >= timeout_bars
      3. structural_compression: (§16)
    Если Exit C сработал:
      wakeup_exit_reason = причина (ttl / no_fresh_candidate / structural_compression)
      если cur_pos != 0:
        state = ST_STOPPING
      иначе:
        state = OFF, счётчики = -1, wakeup_regime_active = 0

  ШАГ 6. Opposite ST flip
    если flip_dir != 0 И state in (ST_ACTIVE_FREEZE, ST_STOPPING):
      если это opposite flip (cur_pos > 0 и flip_dir == -1 ИЛИ cur_pos < 0 и flip_dir == +1):
        wakeup_exit_reason = "opposite_st_flip"
        state = OFF
        held_pos = 0
        wakeup_regime_active = 0
        счётчики = -1

  ШАГ 7. Попытка нового входа (только если state_at_bar_start == OFF и NOT is_reset
          и time_filter_in_window[t])
    _evaluate_wakeup_entry(...) → entry_ok, held_direction
    если entry_ok:
      state = ST_ACTIVE_FREEZE
      held_pos = held_direction
      wakeup_regime_active = 1
      wakeup_cycle_age_bars = 0
      wakeup_confirmed_legs_since_start = 0
      wakeup_bars_since_fresh_candidate = 0 если fresh_candidate_ok иначе 1
      wakeup_exit_reason = "none"

  ШАГ 8. Нормализация ST_STOPPING без позиции (финальный бар / нет cur_pos)
    если state == ST_STOPPING и cur_pos == 0:
      state = OFF, счётчики = -1, wakeup_regime_active = 0

  ШАГ 9. Запись filtered_positions[t+1] (только если t+1 < n)
    если state in (OFF,):                         → next_pos = 0
    если state == ST_ACTIVE_FREEZE:               → next_pos = held_pos
    если state == ST_STOPPING и cur_pos != 0:     → next_pos = cur_pos (hold)
    иначе:                                        → next_pos = 0
```

---

## 15. Entry Mode D: `_evaluate_wakeup_entry`

Все условия AND. Disabled-компонент = passed.

```text
candidate_height_ok =
  entry.candidate_height.enabled == false
  ИЛИ (candidate_height_pct конечный
       И candidate_height_pct >= wakeup_entry_candidate_height_threshold)

candidate_age_ok =
  entry.candidate_age.enabled == false
  ИЛИ (candidate_age_bars != -1
       И candidate_age_bars <= max_bars)

candidate_direction_ok =
  candidate_leg_direction in {-1, +1}

trade_mode_ok =
  _trade_mode_allows_direction(candidate_leg_direction, trade_mode)

atr_ok =
  entry.atr_expansion.enabled == false
  ИЛИ atr_ratio_arr[t] >= min_ratio  (с учётом warmup и non-finite → false)

volume_ok =
  entry.volume_expansion.enabled == false
  ИЛИ volume_ratio_arr[t] >= min_ratio  (с учётом warmup и non-finite → false)

entry_ok = все шесть условий true
held_direction = candidate_leg_direction
```

---

## 16. Exit C

### TTL

```text
ttl_triggered = ttl.enabled И wakeup_cycle_age_bars >= ttl.bars
```

### No Fresh Candidate

```text
fresh_candidate_ok =
  candidate_height_pct конечный
  И candidate_height_pct >= wakeup_no_fresh_candidate_height_threshold
  И candidate_age_bars != -1
  И candidate_age_bars <= max_age_bars

no_fresh_candidate_triggered =
  no_fresh_candidate.enabled
  И wakeup_bars_since_fresh_candidate >= timeout_bars
```

### Structural Compression

```text
effective_local_window =
  structural_compression.local_window  (если не null)
  иначе  trade_filter.zigzag.local_window

structural_triggered =
  structural_compression.enabled
  И wakeup_cycle_age_bars >= min_cycle_age_bars
  И wakeup_confirmed_legs_since_start >= min_confirmed_legs
  И local_median_N доступен и конечный
  И local_median_N < global_median * global_median_multiplier

(если local_median_N недоступен — structural compression не триггерит)
```

### Приоритет

```text
1. ttl
2. no_fresh_candidate
3. structural_compression
```

---

## 17. Reset и opposite ST flip

### Reset

```text
combined_reset_event[t]:
  если wakeup-окно открыто → wakeup_exit_reason = "reset"
  state = OFF, held_pos = 0
  wakeup_regime_active = 0
  все wakeup-счётчики = -1
  reset-бар не может открыть новое wakeup-окно
```

### Opposite ST flip (новое поведение в ST_ACTIVE_FREEZE)

Это поведение отличается от легаси-FREEZE, где flip обновлял `held_pos`.
Для Mode D в `ST_ACTIVE_FREEZE` и `ST_STOPPING`:

```text
opposite flip → close position, state = OFF, счётчики = -1
  wakeup_exit_reason = "opposite_st_flip"

same-direction flip → удерживать held_pos (не реверсить)
neutral → без изменений
```

Реверсов внутри wakeup-окна нет.

---

## 18. Wakeup Counters

```text
wakeup_cycle_age_bars:
  trigger-бар → 0
  инкрементируется каждый бар пока окно открыто
  -1 при OFF/reset

wakeup_confirmed_legs_since_start:
  trigger-бар → 0
  инкрементируется на confirm_event при открытом окне
  -1 при OFF/reset

wakeup_bars_since_fresh_candidate:
  trigger-бар → 0 (если fresh_candidate_ok) или 1 (если нет)
  обновляется каждый бар пока окно открыто
  -1 при OFF/reset
```

Примечание: legacy `confirmed_legs_since_start` для Mode D игнорируется и не
используется для перехода FREEZE→MONITORING (которого у D нет).

---

## 19. Diagnostics

### 19.1 Mode-aware keyset

Легаси-режимы (A/B/C/A+B/C+B) эмитят **ровно тот же набор 41 ключа** без изменений.
Mode D эмитит **свой** набор: общий subset + wakeup-поля.

Существующий strict-equality тест диагностики обновляется: keyset параметризуется
`resolved_mode`. Легаси-ветка теста — без изменений.

Важно: для общих полей Mode D должен использовать те же dtype, что и существующая
легаси-ветка, если поле уже есть в текущем diagnostics contract. Не вводить новые
типы только ради Mode D. Если текущий контракт хранит флаг как `int8`, Mode D тоже
хранит его как `int8`; если строковое поле хранится как `object`, Mode D тоже `object`.

### 19.2 Mode D diagnostic fields

Per-bar:

```text
wakeup_regime_active               int8: 0/1
wakeup_entry_all_ok                int8: 0/1

wakeup_entry_candidate_height_ok   int8: 0/1
wakeup_entry_candidate_age_ok      int8: 0/1
wakeup_entry_candidate_direction_ok int8: 0/1
wakeup_entry_trade_mode_ok         int8: 0/1
wakeup_entry_atr_ok                int8: 0/1
wakeup_entry_volume_ok             int8: 0/1

wakeup_entry_candidate_height_value      float64
wakeup_entry_candidate_height_threshold  float64
wakeup_entry_candidate_age_bars          int64
wakeup_entry_candidate_leg_direction     int8
wakeup_entry_atr_ratio                   float64
wakeup_entry_volume_ratio                float64

wakeup_cycle_age_bars                    int64
wakeup_bars_since_fresh_candidate        int64
wakeup_confirmed_legs_since_start        int64

wakeup_exit_ttl_triggered                int8: 0/1
wakeup_exit_no_fresh_candidate_triggered int8: 0/1
wakeup_exit_structural_compression_triggered int8: 0/1
wakeup_exit_reason                       object (str)
```

Общие поля из легаси, которые Mode D тоже эмитит:

```text
trade_filter_state         object
trade_filter_state_code    same dtype as legacy diagnostics contract
trade_filter_trigger_source object
held_pos_at_bar_start      int8
st_flip_dir                int8
candidate_height_pct       float64
candidate_age_bars         int64
candidate_leg_direction    int8
local_median_N             float64
local_median_available     same dtype as legacy diagnostics contract
global_median              float64
daily_reset_enabled        same dtype as legacy diagnostics contract
daily_reset_event          same dtype as legacy diagnostics contract
time_filter_enabled        same dtype as legacy diagnostics contract
time_filter_in_window      same dtype as legacy diagnostics contract
time_filter_reset_event    same dtype as legacy diagnostics contract
```

`wakeup_exit_reason` values:

```text
"none" | "ttl" | "no_fresh_candidate" | "structural_compression" | "reset" | "opposite_st_flip"
```

### 19.3 Excel display names

Добавить все wakeup-поля в `FILTER_DIAGNOSTICS_100_DISPLAY_NAMES` в
`donor/supertrend_optimizer/io/excel_tester.py` с явными именами (не автоитерация).

---

## 20. Tester Summary

Расширить `filter_diagnostics_summary` (runner.py `_build_filter_diagnostics_summary`):

### 20.1 Counters (из per-bar диагностики)

```text
wakeup_starts_count                        = sum(trigger_source == "wakeup_regime")
wakeup_exit_ttl_count                      = sum(wakeup_exit_ttl_triggered)
wakeup_exit_no_fresh_candidate_count       = sum(wakeup_exit_no_fresh_candidate_triggered)
wakeup_exit_structural_compression_count   = sum(wakeup_exit_structural_compression_triggered)
wakeup_exit_reset_count                    = sum(wakeup_exit_reason == "reset")
wakeup_exit_opposite_st_flip_count         = sum(wakeup_exit_reason == "opposite_st_flip")
wakeup_bars_active                         = sum(wakeup_regime_active)
```

### 20.2 Thresholds sub-dict

```text
wakeup_entry_candidate_height_threshold   (из zigzag_global_stats)
wakeup_no_fresh_candidate_height_threshold (из zigzag_global_stats)
wakeup_entry_candidate_height_quantile    (из config)
wakeup_no_fresh_candidate_quantile        (из config)
```

### 20.3 Config echo

```text
zigzag_mode          = "D"
exit_off_mode        = "exit C"
wakeup_enabled       = true
wakeup_candidate_age_max_bars
wakeup_atr_short_window
wakeup_atr_long_window
wakeup_atr_min_ratio
wakeup_volume_short_window
wakeup_volume_baseline_window
wakeup_volume_min_ratio
wakeup_ttl_bars
wakeup_no_fresh_max_age_bars
wakeup_no_fresh_timeout_bars
wakeup_structural_min_cycle_age_bars
wakeup_structural_min_confirmed_legs
wakeup_structural_local_window
wakeup_structural_global_median_multiplier
wakeup_exit_action_mode
```

Если соответствующий компонент disabled, echo всё равно показывает его config-значение
или sentinel (`None` / `-1` по существующему стилю summary), а counters показывают, что
компонент не срабатывал.

Добавить эти поля в `filters_summary` sheet. Минимальный критерий: по одному Excel/summary
прогону можно восстановить все пороги и основные параметры, которые объясняют wakeup-входы
и Exit C.

---

## 21. Signal Events и Trade Diagnostics

Существующие signal/trade-экспорты остаются backward-compatible.
`trade_filter_trigger_source = "wakeup_regime"` потребляется signal-экспортом через
существующий mapping — проверить и при необходимости добавить в explicit-mapping.
Trade-level `exit_reason` в Phase 0 не расширяется.

---

## 22. Тесты

### 22.1 Config Schema и Caller Gate

```text
tester принимает D + exit C + wakeup_regime.enabled=true
tester отвергает D без exit C
tester отвергает D без wakeup_regime.enabled=true
tester отвергает exit C с mode != D
tester отвергает exit C + exit_off_zz_leg_count
tester отвергает exit C + exit_b_immediate_off
wf_grid отвергает Mode D
wf_grid отвергает exit C
wf_grid отвергает wakeup_regime
старые modes/exits остаются допустимыми
```

### 22.2 Валидация полей

```text
каждое enabled-поле: non-bool отвергается
каждый quantile: вне (0,1) отвергается
каждое окно/bars: < 1 отвергается
min_confirmed_legs: < 0 отвергается
min_ratio / global_median_multiplier: <= 0 отвергается
volume_expansion.baseline_window < short_window отвергается
structural_compression.local_window: null принимается, 0 отвергается
exit.action.mode: не "block_new_entries" отвергается
```

### 22.3 Global Stats

```text
wakeup_entry_candidate_height_threshold вычисляется из config-квантиля
wakeup_no_fresh_candidate_height_threshold вычисляется из config-квантиля
min-legs проверка только при enabled Mode D wakeup-компонентах
disabled компонент → порог не вычисляется и не требует min-legs
легаси-режимы не падают из-за wakeup-квантилей
```

### 22.4 Runtime Metrics

```text
ATR ratio использует calculate_true_range + calculate_atr_rma
ATR warmup блокирует до long_window - 1
ATR NaN/Inf/non-finite → atr_ok=false
volume ratio использует _rolling_aggregate
volume warmup → volume_ok=false
volume baseline zero → volume_ok=false
volume NaN/Inf/non-finite → volume_ok=false
disabled entry-компонент всегда passed
disabled entry-компонент с NaN-данными всё равно passed
```

### 22.5 Entry Mode D

```text
candidate_height гейтит вход (enabled=true)
candidate_age гейтит вход (enabled=true)
candidate_direction гейтит вход (enabled=true)
trade_mode гейтит направление
ATR гейтит вход
volume гейтит вход
все компоненты комбинируются как AND
candidate_age == -1 блокирует
candidate_direction == 0 блокирует
time_filter вне окна блокирует
combined_reset бар блокирует
legacy candidate_trigger_threshold не гейтит Mode D
legacy trade_filter.volume gate не блокирует Mode D
disabled компонент не влияет на вход
```

### 22.6 FSM и поведение позиции

```text
Mode D входит немедленно из OFF (нет WAIT)
нет WAIT_FIRST_ST_FLIP
UP candidate открывает long
DOWN candidate открывает short
позиция появляется на t+1 (open-to-open)
same-direction ST flip удерживает held_pos
opposite ST flip из ST_ACTIVE_FREEZE закрывает позицию (не реверсит)
opposite ST flip из ST_STOPPING закрывает позицию
нет внутренних реверсов
нет ST_ACTIVE_MONITORING для Mode D
legacy median-stop не закрывает wakeup-окно
legacy FSM-цикл не исполняется для Mode D
```

### 22.7 Wakeup Counters

```text
trigger-бар: age=0, confirmed_legs=0
следующий бар: age=1
confirm_event инкрементирует только после открытия окна
OFF/reset: все счётчики = -1
ST_STOPPING продолжает инкрементировать age и no-fresh
TTL-граница без off-by-one
wakeup_bars_since_fresh_candidate на trigger-баре: 0 или 1
```

### 22.8 Exit C

```text
TTL триггерит при age >= ttl.bars
no_fresh_candidate триггерит при bars_since_fresh >= timeout_bars
structural_compression триггерит при выполнении всех условий
приоритет: ttl > no_fresh_candidate > structural_compression
Exit C с позицией → ST_STOPPING
Exit C без позиции → OFF + счётчики = -1
ST_STOPPING удерживает позицию до opposite ST flip
ST_STOPPING запрещает новые входы и реверсы
structural_compression не триггерит при недоступном local_median_N
disabled exit-компонент никогда не триггерит
```

### 22.9 Reset

```text
combined_reset закрывает wakeup-окно
reset при открытом окне → wakeup_exit_reason="reset"
reset очищает счётчики в -1
reset-бар не открывает новое окно
```

### 22.10 Diagnostics и Summary

```text
все wakeup-поля присутствуют в Mode D diagnostics
длина каждого массива == n
dtype/object значения соответствуют спецификации
общие Mode D diagnostics fields используют dtype текущего legacy diagnostics contract
FilterDiagnostics_100 display names включают wakeup-поля
filter_diagnostics_summary включает wakeup counters и thresholds
filter_diagnostics_summary включает wakeup quantiles и config echo
filters_summary позволяет восстановить wakeup thresholds/gates/Exit C параметры
wakeup_starts_count совпадает с count(trigger_source=="wakeup_regime")
wakeup_bars_active совпадает с sum(wakeup_regime_active)
mode-aware keyset: легаси-режимы получают ровно 41 ключ (без изменений)
```

### 22.11 Regression

```text
Mode A без изменений
Mode B без изменений
Mode C без изменений
Mode A+B без изменений
Mode C+B без изменений
exit A без изменений
exit B без изменений
exit_b_immediate_off без изменений
time_filter без изменений
daily_reset без изменений
trade_filter.volume без изменений
apply() без high/low/volume продолжает работать
```

---

## 23. Финальная верификация

Запустить:

```text
donor config/schema тесты
donor ZigZag runtime тесты
donor Excel/summary тесты
shared wf_grid schema тесты (trade_filter_config.py)
существующие ZigZag FSM/global stats regression тесты
существующие volume gate regression тесты
```

Single-config tester сценарий:

```yaml
zigzag.mode: D
lifecycle.exit_off_mode: "exit C"
wakeup_regime.enabled: true
```

Criteria:

```text
wakeup_starts_count > 0
вход без ожидания ST flip
позиции следуют candidate_leg_direction
Exit C закрывает wakeup-окно
ST_STOPPING блокирует новые входы и реверсы
opposite ST flip закрывает без реверса
легаси modes/exits без изменений
wf_grid отвергает Phase 0 wakeup config
```

---

## 24. Порядок реализации

```text
1.  Baseline: зафиксировать зелёные тесты
2.  Config schema: dataclass'ы + whitelist + __all__
3.  Caller gate + full config validation + _validate_wakeup_regime_block
4.  Config тесты (§22.1, §22.2)
5.  Global stats: config-driven thresholds + conditional min-legs
6.  Global stats тесты (§22.3)
7.  Runtime data plumbing: volume через run_period → run_single_backtest → run_backtest_fast → apply
8.  Runtime metric helpers: _compute_wakeup_atr_ratio, _compute_wakeup_volume_ratio
9.  Runtime metric тесты (§22.4)
10. Точка диспетча в apply(): early-return для Mode D
11. _run_wakeup_fsm: per-bar цикл (шаги 0–9 из §14)
12. _evaluate_wakeup_entry (§15)
13. Entry тесты (§22.5)
14. FSM и позиция тесты (§22.6)
15. Wakeup counters тесты (§22.7)
16. Exit C логика (§16)
17. Exit C тесты (§22.8)
18. Reset и opposite ST flip (§17)
19. Reset тесты (§22.9)
20. Diagnostics fields + mode-aware keyset контракт
21. Tester summary + thresholds/config echo sub-dict
22. Excel display names
23. Diagnostics/summary тесты (§22.10)
24. Regression тесты (§22.11)
25. Single-config tester верификация (§23)
```

---

## 25. Out of Scope

```text
WF Grid runtime поддержка Mode D
WF export wakeup-полей
parallel runtime transport изменения
WF OOS валидация
force_flat
effective-config source tracking
trade-level exit_reason расширение (если нет отдельных тестов)
полный Excel polish сверх диагностики/summary
signal_events редизайн
```

---

## 26. Риски и митигации

| Риск | Митигация |
|------|-----------|
| Легаси-регрессии A/B/C/A+B/C+B | Изоляция: легаси-цикл для D не исполняется физически |
| «Hold до opposite flip» в FREEZE реализован неверно | Явный тест: opposite flip из ST_ACTIVE_FREEZE → close, не reverse |
| Strict keyset (41 ключ) сломается | Mode-aware keyset: легаси-ветка теста не меняется |
| Volume-плумбинг недоспецифицирован | Явный список: run_period/run_single_backtest/run_backtest_fast/apply — все сигнатуры |
| Mode D / wakeup утечёт в wf_grid | Caller gate + wf_grid reject тесты |
| Противоречие ТЗ §6 vs имена полей | Закрыто правкой ТЗ: config-derived имена, без p65/p60 |
| Двойной счётчик confirmed_legs | Явно задокументировано в §18: legacy-счётчик для D игнорируется |
| ATR/volume warmup после reset | Закрыто в §10.4: warmup от бара 0, reset не перезапускает |
| Off-by-one в Exit C TTL | Явный тест: trigger-бар age=0, Exit C оценивается с age=1 |
