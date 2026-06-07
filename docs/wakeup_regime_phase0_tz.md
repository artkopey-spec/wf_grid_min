Ниже пересобранная финальная версия ТЗ с учётом всех последних правок.

---

> Status: superseded historical TZ.
> Use `docs/wakeup_regime_phase0_implementation_plan_v5_1.md` as the
> authoritative Phase 0 source of truth. This file is retained for context only.
> Known superseded points:
> - `wf_grid` is not fully untouched anymore: v5.1 allows schema/reject-layer
>   and shared-validator compatibility changes, while still forbidding Mode D
>   runtime/export/parallel transport in `wf_grid`.
> - `structural_compression` is out of scope for Phase 0.
> - Acceptance must cover both `block_new_entries` and `close_position`.

# ТЗ на Реализацию Phase 0: `wakeup_regime` в `donor/tester`

## 1. Цель

Реализовать прототип `wakeup_regime` в `donor/supertrend_optimizer/core` и проверить торговую гипотезу через single-config tester.

Гипотеза:

```text
свежий импульс цены + ATR + объёма
→ немедленный вход в направлении импульсной ZigZag candidate leg
→ ограниченное окно сопровождения
→ выключение окна, когда импульс состарился или структура сжалась
```

Phase 0 реализуется только в `donor/`.

Не трогаем:

```text
wf_grid
parallel execution
WF export
production-grade effective config
WF OOS validation
```

Код в `donor/supertrend_optimizer/core` пишется как reusable core, не throwaway.

---

## 2. Архитектурные Решения

1. `Mode D` — отдельный ZigZag entry mode.
2. `Exit C` — отдельный lifecycle exit mode.
3. `Mode D` разрешён только с `exit C`.
4. `exit C` разрешён только с `Mode D`.
5. Старые modes `A`, `B`, `C`, `A+B`, `C+B` не меняются.
6. `Mode D` не ждёт SuperTrend flip для входа.
7. `Mode D` входит сразу в направлении `candidate_leg_direction`.
8. SuperTrend используется после входа для сопровождения и закрытия позиции.
9. `force_flat` в Phase 0 не реализуется.
10. Default action для `exit C` — `block_new_entries`.

---

## 3. Целевой Config

Важно: `reversal_threshold`, `candidate_trigger_threshold` и `local_window` остаются обязательными для текущего ZigZag pipeline.

`candidate_trigger_threshold` в Phase 0 для `Mode D` **не используется для входа**, но остаётся обязательным ради совместимости с текущими validator/build path.

```yaml
trade_filter:
  enabled: true
  type: zigzag_st_mode

  zigzag:
    enabled: true
    mode: D

    # Required by existing ZigZag global/per-bar pipeline.
    reversal_threshold: 0.005
    candidate_trigger_threshold: 0.012
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

      structural_compression:
        enabled: true
        min_cycle_age_bars: 15
        min_confirmed_legs: 2
        local_window: null
        global_median_multiplier: 0.9

      action:
        mode: block_new_entries
```

---

## 4. Config Schema

Изменить только:

```text
donor/supertrend_optimizer/core/trade_filter_config.py
```

Добавить:

```text
_VALID_ZIGZAG_MODES += "D"
exit_off_mode literals += "exit C"
```

Добавить dataclasses:

```text
TradeFilterWakeupRegimeConfig
TradeFilterWakeupEntryConfig
TradeFilterWakeupExitConfig
TradeFilterWakeupCandidateHeightConfig
TradeFilterWakeupCandidateAgeConfig
TradeFilterWakeupAtrExpansionConfig
TradeFilterWakeupVolumeExpansionConfig
TradeFilterWakeupTtlExitConfig
TradeFilterWakeupNoFreshCandidateExitConfig
TradeFilterWakeupStructuralCompressionExitConfig
TradeFilterWakeupExitActionConfig
```

Добавить поле:

```text
TradeFilterConfig.wakeup_regime
```

Добавить parsing в:

```text
build_trade_filter_config_from_raw
```

Добавить donor whitelist keys для всей ветки:

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

`wf_grid` в Phase 0 не менять.

---

## 5. Validator Rules

Обязательные guards:

```text
zigzag.mode == "D" требует wakeup_regime.enabled == true
zigzag.mode == "D" требует lifecycle.exit_off_mode == "exit C"
lifecycle.exit_off_mode == "exit C" требует zigzag.mode == "D"
```

Если `mode: D`, но `exit_off_mode` отсутствует в raw config — `ConfigError`.

Если `mode: D`, но `wakeup_regime.enabled` отсутствует или не `true` — `ConfigError`.

Если `exit_off_mode: "exit C"` и присутствует `exit_off_zz_leg_count` — `ConfigError`.

`candidate_trigger_threshold`:

```text
Phase 0: остаётся обязательным для Mode D,
но Mode D его не использует для entry logic.
```

`freeze_confirmed_legs`:

```text
при Mode D принимается,
но не управляет остановкой,
старый median-stop lifecycle для D отключён.
```

---

## 6. Global Stats

В `ZigZagGlobalStats` добавить два порога, выведенных из config:

```text
wakeup_entry_candidate_height_threshold
wakeup_no_fresh_candidate_height_threshold
```

Имена полей не содержат числовых квантилей — значения берутся из config:

```python
wakeup_entry_candidate_height_threshold = np.quantile(
    confirmed_heights_pct,
    wakeup_regime.entry.candidate_height.quantile   # из config, например 0.65
)

wakeup_no_fresh_candidate_height_threshold = np.quantile(
    confirmed_heights_pct,
    wakeup_regime.exit.no_fresh_candidate.quantile  # из config, например 0.60
)
```

Оба порога вычисляются только если:

```text
zigzag.mode == "D"
и соответствующий компонент enabled == true
```

Если компонент `enabled: false` — его порог не вычисляется и не хранится.

Источник:

```text
full-dataset confirmed ZigZag heights
```

Минимум ног (только для включённых компонентов):

```text
min_legs_for_wakeup_quantile = max(local_window, 10)
```

Если хотя бы один включённый wakeup-компонент требует квантиль, и:

```text
n_legs_total < min_legs_for_wakeup_quantile
```

то:

```text
ConfigError
```

Легаси-режимы (A, B, C, A+B, C+B) не затрагиваются этой проверкой.

Это консистентно с существующим механизмом `candidate_trigger_threshold = "auto"`.

---

## 7. Runtime Metrics

Метрики Phase 0 считаются inline внутри core-фильтра.

### Candidate

Использовать существующие `ZigZagPerBar` поля:

```text
candidate_height_pct
candidate_age_bars
candidate_leg_direction
local_median_N
local_median_available
confirm_event
```

Rules:

```text
candidate_leg_direction == +1 -> long
candidate_leg_direction == -1 -> short
candidate_leg_direction == 0 -> entry fail-closed
candidate_age_bars == -1 -> candidate_age failed
candidate_height NaN/non-finite -> candidate_height failed
```

### ATR Expansion

Расширить `apply()` backward-compatible:

```python
high: Optional[np.ndarray] = None
low: Optional[np.ndarray] = None
volume: Optional[np.ndarray] = None
```

`high/low` обязательны только если:

```text
mode == D
entry.atr_expansion.enabled == true
```

TR:

```text
calculate_true_range(high, low, close)
```

ATR:

```text
calculate_atr_rma-compatible RMA
```

Ratio:

```text
atr_ratio = ATR_short / ATR_long
```

Entry rule:

```text
atr_ok = false, если bar_index < long_window - 1
atr_ok = false, если ratio NaN/inf/non-finite
atr_ok = atr_ratio >= min_ratio иначе
```

### Volume Expansion

`volume` обязателен только если:

```text
mode == D
entry.volume_expansion.enabled == true
```

Volume expansion не включает старый `trade_filter.volume`.

Ratio:

```text
volume_short = rolling aggregate(volume, short_window)
volume_baseline = rolling aggregate(volume, baseline_window)
volume_ratio = volume_short / volume_baseline
```

Entry rule:

```text
volume_ok = false, если baseline <= 0
volume_ok = false, если baseline NaN/inf/non-finite
volume_ok = false, если ratio NaN/inf/non-finite
volume_ok = volume_ratio >= min_ratio иначе
```

---

## 8. Entry Mode D

Entry D стартует только из `OFF`.

Entry conditions работают как AND:

```text
candidate_height_pct >= wakeup_entry_candidate_height_threshold  (из config quantile)
candidate_age_bars <= max_bars                                    (из config max_bars)
candidate_leg_direction is +1 or -1
trade_mode allows candidate_leg_direction
atr_ratio >= min_ratio                                           (из config min_ratio)
volume_ratio >= min_ratio                                        (из config min_ratio)
```

Disabled entry component считается passed.

Unknown/недоступные данные enabled-компонента считаются failed.

Entry D учитывает:

```text
не стартовать на combined reset bar
не стартовать вне time_filter window
старт только из OFF
```

Старый dispatcher `volume_allowed` не должен блокировать Mode D. Для Mode D wakeup-volume является единственным volume-условием entry.

FSM semantics:

```text
OFF -> ST_ACTIVE_FREEZE immediately
```

У Mode D нет `WAIT_FIRST_ST_FLIP`.

При входе:

```text
held_pos = candidate_leg_direction
confirmed_legs_since_start = 0
wakeup_cycle_age_bars = 0
wakeup_regime_active = 1
trade_filter_trigger_source = "wakeup_regime"
```

Позиция появляется по existing open-to-open model:

```text
trigger detected at close(t)
position active at open(t+1)
```

---

## 9. SuperTrend Behavior Inside Wakeup Window

Рекомендуемая Phase 0 семантика:

```text
после immediate entry позиция удерживается до opposite ST flip;
реверсы внутри wakeup window не обязательны и в Phase 0 не реализуются;
новый вход возможен только после возврата в OFF и нового wakeup trigger.
```

То есть:

```text
UP candidate -> long
DOWN candidate -> short
opposite ST flip -> close position
same-direction / neutral ST movement -> hold
```

---

## 10. Wakeup Counters

`wakeup_cycle_age_bars`:

```text
стартует с 0 на баре wakeup trigger
монотонно растёт в ST_ACTIVE_FREEZE
монотонно растёт в ST_STOPPING
сбрасывается в -1 при OFF/reset
```

`wakeup_confirmed_legs_since_start`:

```text
стартует с 0 на wakeup trigger
инкрементируется на confirm_event
используется structural_compression
сбрасывается в -1 при OFF/reset
```

`wakeup_bars_since_fresh_candidate`:

```text
стартует на wakeup trigger
обновляется каждый бар, пока окно открыто
сбрасывается в -1 при OFF/reset
```

---

## 11. Exit C Logic

Exit C проверяется, пока wakeup window открыт:

```text
ST_ACTIVE_FREEZE
ST_STOPPING
```

Exit C conditions работают как OR.

Priority:

```text
1. ttl
2. no_fresh_candidate
3. structural_compression
```

### TTL

```text
ttl_triggered = wakeup_cycle_age_bars >= ttl.bars
```

### No Fresh Candidate

```text
fresh_candidate_ok =
  candidate_height_pct >= wakeup_no_fresh_candidate_height_threshold  (из config quantile)
  AND candidate_age_bars <= max_age_bars                               (из config max_age_bars)
```

Unavailable candidate data:

```text
fresh_candidate_ok = false
```

Counter:

```text
on wakeup trigger:
  bars_since_fresh_candidate = 0 if fresh_candidate_ok else 1

on each next bar while wakeup window open:
  if fresh_candidate_ok:
    bars_since_fresh_candidate = 0
  else:
    bars_since_fresh_candidate += 1
```

Trigger:

```text
no_fresh_candidate_triggered =
  bars_since_fresh_candidate >= timeout_bars
```

### Structural Compression

```text
structural_triggered =
  wakeup_cycle_age_bars >= min_cycle_age_bars
  AND wakeup_confirmed_legs_since_start >= min_confirmed_legs
  AND local_median_N is available
  AND local_median_N < global_median * global_median_multiplier
```

Если `local_median_N` недоступен:

```text
structural_compression does not trigger
```

Если:

```yaml
local_window: null
```

то использовать:

```text
trade_filter.zigzag.local_window
```

---

## 12. Exit C Action: `block_new_entries`

При Exit C:

```text
FSM -> ST_STOPPING
new entries forbidden
reversals forbidden
current position is held until opposite ST flip
```

Если позиции уже нет:

```text
FSM -> OFF
wakeup counters reset
```

Exit C detected at close(t), effect applies through existing open-to-open position model.

`force_flat` не реализуется.

---

## 13. Reset Behavior

На `combined_reset_event`:

```text
FSM -> OFF
held_pos = 0
wakeup_regime_active = 0
wakeup_cycle_age_bars = -1
wakeup_bars_since_fresh_candidate = -1
wakeup_confirmed_legs_since_start = -1
```

Если reset закрывает открытое wakeup window, записать:

```text
wakeup_exit_reason = "reset"
```

Reset bar не может открыть новое wakeup window.

---

## 14. Ordering Requirements

Для `Mode D` старый median-stop блок должен быть отключён.

Обязательное правило:

```text
Mode D не закрывается старым условием local_median_N < global_median.
Exit C — единственный lifecycle exit для wakeup window,
кроме reset/time-filter wipe и opposite ST flip position close.
```

Runtime должен явно gate’ить старую median-stop ветку:

```text
not is_exit_c
```

или эквивалентно.

---

## 15. Diagnostics

Добавить per-bar diagnostics:

```text
wakeup_regime_active
wakeup_entry_all_ok

wakeup_entry_candidate_height_ok
wakeup_entry_candidate_age_ok
wakeup_entry_candidate_direction_ok
wakeup_entry_atr_ok
wakeup_entry_volume_ok

wakeup_entry_candidate_height_value
wakeup_entry_candidate_height_threshold
wakeup_entry_candidate_age_bars
wakeup_entry_candidate_leg_direction
wakeup_entry_atr_ratio
wakeup_entry_volume_ratio

wakeup_cycle_age_bars
wakeup_bars_since_fresh_candidate
wakeup_confirmed_legs_since_start

wakeup_exit_ttl_triggered
wakeup_exit_no_fresh_candidate_triggered
wakeup_exit_structural_compression_triggered
wakeup_exit_reason
```

`wakeup_exit_reason` values:

```text
"none"
"ttl"
"no_fresh_candidate"
"structural_compression"
"reset"
"opposite_st_flip"
```

Tester `FilterDiagnostics_100` должен подхватить новые поля из diagnostics dict.

---

## 16. Tester Summary

Минимально добавить в tester summary / `filters_summary`:

```text
zigzag_mode
exit_off_mode
wakeup_starts_count
wakeup_exit_ttl_count
wakeup_exit_no_fresh_candidate_count
wakeup_exit_structural_compression_count
wakeup_exit_opposite_st_flip_count
wakeup_bars_active
```

---

## 17. Tests

### Config

```text
D + exit C + wakeup_regime.enabled=true accepted
D without wakeup_regime.enabled=true rejected
D without raw exit_off_mode="exit C" rejected
exit C with mode != D rejected
exit C + exit_off_zz_leg_count rejected
D still requires reversal_threshold
D still requires candidate_trigger_threshold in Phase 0
old modes with exit A/B still accepted
```

### Global Stats

```text
wakeup_entry_candidate_height_threshold computed from config quantile + confirmed_heights_pct
wakeup_no_fresh_candidate_height_threshold computed from config quantile + confirmed_heights_pct
min-leg check only applies to enabled Mode D wakeup quantile components
n_legs_total < max(local_window, 10) rejected (only when enabled component requires quantile)
old modes do not fail because of wakeup quantile checks
```

### Entry

```text
candidate_height gates entry
candidate_age gates entry
candidate_direction gates entry
trade_mode disallows direction -> no entry
atr_expansion gates entry
volume_expansion gates entry
disabled entry component passes
entry components combine as AND
candidate_age == -1 fails entry
candidate_direction == 0 fails entry
ATR warmup fails until long_window - 1
volume baseline NaN/zero/non-finite fails
old volume_allowed gate does not block Mode D
```

### Position Behavior

```text
Mode D enters immediately in candidate_leg_direction
UP candidate opens long
DOWN candidate opens short
position appears at t+1 under open-to-open model
opposite ST flip closes position
no WAIT_FIRST_ST_FLIP state for Mode D
no internal reversals in Phase 0
```

### Exit C

```text
TTL exit triggers
no_fresh_candidate exit triggers
structural_compression exit triggers
exit reason priority: ttl > no_fresh_candidate > structural_compression
Exit C with position enters ST_STOPPING/block_new_entries
ST_STOPPING holds position until opposite ST flip
ST_STOPPING forbids reversals/new entries
Exit C with no position returns OFF
```

### Reset

```text
combined reset closes wakeup window
reset writes wakeup_exit_reason="reset" when it closes active wakeup window
reset clears wakeup counters to -1
reset bar cannot open wakeup window
```

### Regression

```text
existing Mode A/B/C/A+B/C+B golden tests unchanged
existing exit A/B behavior unchanged
existing volume gate behavior unchanged
old apply() callers without high/low/volume still work
```

---

## 18. Out Of Scope Phase 0

Не делать:

```text
wf_grid integration
wf_grid config/schema/export changes
parallel runtime transport
WakeupAtrRuntime / WakeupVolumeRuntime infrastructure
effective config source tracking
force_flat
full Excel polish
signal_events expansion
trade-level exit_reason expansion
WF OOS validation
```

---

## 19. Success Criteria

Phase 0 успешна, если tester показывает:

```text
Mode D открывает позиции сразу в направлении impulse leg
Exit C закрывает wakeup window
wakeup_starts_count > 0
exit reasons распределены осмысленно
нет ожидания ST flip перед входом
block_new_entries работает корректно
старые modes/exits не изменились
```

Важно:

```text
Phase 0 использует full-dataset thresholds.
Положительный результат может содержать in-sample lookahead.
Финальная проверка edge — только через WF OOS в Phase 1.
```
