# Wakeup Regime Phase 0 - Implementation Plan v5.1

> Status: authoritative Phase 0 implementation plan.
> This document supersedes `docs/wakeup_regime_phase0_tz.md` wherever they
> differ. In particular, Phase 0 allows `wf_grid` schema/reject-layer updates
> and keeps `structural_compression` out of scope.

Этот документ является самостоятельным финальным планом реализации Phase 0.
Он не требует чтения предыдущих версий и не является patch-описанием поверх
старого плана. Предыдущие версии остаются в `docs/` только для истории.

---

## 1. Цель

Реализовать в `donor`-tester прототип режима `wakeup_regime` для проверки
гипотезы:

```text
свежий ZigZag candidate impulse
+ ATR expansion
+ volume expansion
-> немедленный вход по направлению candidate leg
-> ограниченное окно жизни wakeup-цикла
-> завершение окна по Exit C conditions
```

Главное отличие от legacy A/B/C: Mode D не ждёт SuperTrend flip для старта
позиции. Направление входа берётся из `candidate_leg_direction`.

Минимальный критерий успеха:

- single-config tester принимает `zigzag.mode: D`;
- `wf_grid` отвергает `mode: D`, `exit C` и `wakeup_regime`;
- Mode D входит из `OFF` сразу по candidate direction;
- Mode D не исполняет legacy FSM loop A/B/C/A+B/C+B;
- `wakeup_starts_count > 0` на acceptance-прогоне;
- Exit C поддерживает оба action mode:
  `block_new_entries` и `close_position`;
- legacy modes A/B/C/A+B/C+B и exits A/B не меняют поведение;
- Excel/export/signal/trade diagnostics не падают и корректно отражают wakeup.

---

## 2. Scope и source of truth

Единственное рабочее дерево Phase 0:

```text
donor/supertrend_optimizer/
```

`donor TESTER/` не используется и не меняется. Это исторический mirror.

Разрешённые области изменений:

```text
donor/supertrend_optimizer/core/trade_filter_config.py
donor/supertrend_optimizer/core/zigzag_st_filter.py
donor/supertrend_optimizer/core/backtest.py
donor/supertrend_optimizer/core/filter_trade_diagnostics.py
donor/supertrend_optimizer/data/validator.py
donor/supertrend_optimizer/engine/run.py
donor/supertrend_optimizer/engine/result.py
donor/supertrend_optimizer/testing/runner.py
donor/supertrend_optimizer/testing/signal_events.py
donor/supertrend_optimizer/io/excel_tester.py
donor/supertrend_optimizer/cli/tester.py
donor/tests/**
wf_grid/config/loader.py
wf_grid/tests/**
```

Правки `wf_grid` разрешены только для schema/reject слоя и shared-validator
совместимости. Runtime Mode D, WF OOS Mode D, WF export wakeup-полей и
parallel transport для Mode D запрещены в Phase 0.

Запрещено в Phase 0:

```text
WF Grid runtime support for Mode D
WF Grid export wakeup diagnostics
WF OOS Mode D
parallel/transport changes for Mode D
production effective-config tracking
force_flat/global flatten
hard-close with immediate reversal
structural_compression exit
редизайн общего trade-level exit_reason контракта
```

---

## 3. Архитектурное решение

### 3.1 Общий принцип

Mode D изолируется от legacy transition logic, но не копирует весь runtime
контракт заново.

Правильная форма реализации:

```text
apply()
  -> resolve common runtime inputs
  -> compute/reset/time_filter/per_bar
  -> materialize common diagnostics allocation
  -> if resolved_mode == "D":
         run Mode D transitions on common arrays
         finalize diagnostics
         return
     else:
         run legacy A/B/C/A+B/C+B transitions
         finalize diagnostics
         return
```

Нельзя делать `_run_wakeup_fsm` как полностью автономную ветку со своим
несвязанным diagnostics keyset. Это приведёт к расхождению с Excel, summary,
`BacktestResult` и legacy tests.

### 3.2 Обязательные helper boundaries

В `zigzag_st_filter.py` перед реализацией Mode D выделить или явно оформить
следующие внутренние helper-блоки:

```text
_resolve_common_filter_inputs(...)
  trend, close, per_bar, daily_reset_event, time_filter_events,
  combined_reset_event, resolved_mode, common scalar echoes

_allocate_common_diagnostics(n, ...)
  все существующие legacy-compatible arrays с правильными dtype/sentinel

_finalize_filter_diagnostics(common, mode_specific)
  объединяет common keyset + optional volume fields + wakeup fields

_run_legacy_fsm(...)
  текущая логика A/B/C/A+B/C+B без изменения поведения

_run_wakeup_fsm(...)
  только Mode D transition logic, пишет в common arrays и wakeup arrays
```

Рефакторинг должен быть минимальным и механическим: вынести setup/allocation,
не менять legacy state transitions.

### 3.3 Common diagnostics contract

Для Mode D должны присутствовать все common keys, которые сейчас ожидают
tester summary, Excel, signal events и `BacktestResult`.

Минимальный common keyset:

```text
trade_filter_state
trade_filter_state_code
trade_filter_trigger_source
confirmed_legs_since_start
st_flip_dir
trade_filter_enabled
zigzag_reversal_threshold
candidate_height_pct
candidate_trigger_threshold
local_median_N
local_median_available
local_window
global_median
global_stats_available
freeze_confirmed_legs
median_stop_triggered
stopping_started_at_index
filter_allowed_entry
filter_block_reason
exit_off_mode
exit_off_zz_leg_count
zz_legs_since_lifecycle_start
zz_leg_stop_triggered
exit_b_immediate_off_triggered
exit_b_immediate_off_config
daily_reset_enabled
daily_reset_event
time_filter_enabled
time_filter_in_window
time_filter_reset_event
candidate_threshold_ok
candidate_component_ok
confirmed_median_ok
b_component_ok
immediate_allowed
candidate_duration_gate_passed
state_at_bar_start
held_pos_at_bar_start
confirmed_legs_at_bar_start
zigzag_mode
candidate_age_bars
candidate_leg_direction
candidate_duration_gate_enabled
candidate_duration_max_bars
immediate_candidate_entry_used
immediate_candidate_entry_block_reason
```

Mode D fills legacy-only counters with stable sentinel values:

```text
confirmed_legs_since_start = -1 except where existing summary needs active-cycle count
median_stop_triggered = 0
zz_leg_stop_triggered = 0
exit_b_immediate_off_triggered = 0
exit_b_immediate_off_config = 0
immediate_candidate_entry_used = 0
immediate_candidate_entry_block_reason = "mode_not_c"
```

`exit_off_mode` echo for Mode D is `"exit C"`.

---

## 4. Config model

### 4.1 Target YAML

```yaml
trade_filter:
  enabled: true
  type: zigzag_st_mode

  zigzag:
    enabled: true
    mode: D
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
      action:
        mode: block_new_entries
```

Allowed action modes:

```text
block_new_entries
close_position
```

`exit_off_mode: "exit C"` задаёт набор условий выхода из wakeup-окна.
Конкретное действие при срабатывании задаёт
`wakeup_regime.exit.action.mode`.

### 4.2 Dataclasses

Добавить в `trade_filter_config.py`:

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

В `TradeFilterConfig`:

```python
wakeup_regime: Optional[TradeFilterWakeupRegimeConfig] = None
```

Правила dataclass defaults:

- числовые поля по умолчанию `None`;
- `enabled` defaults: component-level `False`, top-level absent means `None`;
- значения хранятся как из YAML без coercion;
- coercion запрещён до validation, чтобы type mismatch был точным.

### 4.3 Allowed-key schema

Обновить оба schema слоя:

```text
donor/supertrend_optimizer/core/trade_filter_config.py
wf_grid/config/loader.py
```

Почему оба: `wf_grid` сначала проверяет локальный `_ALLOWED_KEYS`, и только
после этого вызывает shared collector/validator. Если обновить только donor,
`wf_grid` будет отвергать wakeup keys не тем слоем.

Добавить paths:

```text
trade_filter -> wakeup_regime

trade_filter.wakeup_regime
  -> enabled, entry, exit

trade_filter.wakeup_regime.entry
  -> candidate_height, candidate_age, atr_expansion, volume_expansion

trade_filter.wakeup_regime.entry.candidate_height
  -> enabled, quantile

trade_filter.wakeup_regime.entry.candidate_age
  -> enabled, max_bars

trade_filter.wakeup_regime.entry.atr_expansion
  -> enabled, short_window, long_window, min_ratio

trade_filter.wakeup_regime.entry.volume_expansion
  -> enabled, short_window, baseline_window, min_ratio

trade_filter.wakeup_regime.exit
  -> ttl, no_fresh_candidate, action

trade_filter.wakeup_regime.exit.ttl
  -> enabled, bars

trade_filter.wakeup_regime.exit.no_fresh_candidate
  -> enabled, quantile, max_age_bars, timeout_bars

trade_filter.wakeup_regime.exit.action
  -> mode
```

---

## 5. Validation

### 5.1 Mode literals

Расширить:

```text
_VALID_ZIGZAG_MODES += "D"
exit_off_mode allowed literals += "exit C"
```

Сообщения об ошибках должны быть обновлены так, чтобы allowed literals
включали новые значения.

### 5.2 Caller gate

`caller_pipeline == "tester"`:

```text
mode D allowed
exit C allowed only with mode D
wakeup_regime allowed only with mode D
```

`caller_pipeline == "wf_grid"`:

```text
zigzag.mode == "D" -> ConfigError
lifecycle.exit_off_mode == "exit C" -> ConfigError
wakeup_regime present -> ConfigError
```

Важно: caller-specific differences должны применяться только к Phase 0
features. Старые A/B/C/A+B/C+B validation errors должны оставаться
одинаковыми для `wf_grid` и `tester`, кроме уже существующих различий.

### 5.3 Cross-field guards

```text
mode D требует raw key lifecycle.exit_off_mode == "exit C"
mode D требует wakeup_regime.enabled == true
mode D требует candidate_trigger_threshold numeric
mode D отвергает candidate_trigger_threshold == "auto"
mode D отвергает candidate_trigger_quantile

exit C требует mode D
exit C отвергает exit_off_zz_leg_count
exit C отвергает exit_b_immediate_off
exit C отвергает legacy triggers block

mode != D отвергает wakeup_regime, если wakeup_regime присутствует
wakeup_regime.enabled == true требует mode D
```

### 5.4 Wakeup field validation

General:

```text
enabled must be bool
quantile must be finite numeric, 0 < q < 1
bars/max_bars/max_age_bars/timeout_bars must be int >= 1
short_window/long_window/baseline_window must be int >= 1
min_ratio must be finite numeric > 0
atr.long_window >= atr.short_window
volume.baseline_window >= volume.short_window
exit.action.mode in {"block_new_entries", "close_position"}
```

Enabled component:

```text
all required numeric fields must be present and valid
```

Disabled component:

```text
numeric fields may be absent
runtime treats disabled entry component as passed
runtime treats disabled exit component as never fires
```

Top-level wakeup:

```text
wakeup_regime.enabled == true requires entry and exit mappings
exit.action.mode required when wakeup_regime.enabled == true
at least one entry component must be enabled
at least one exit condition must be enabled
```

---

## 6. Resolvers

Existing resolver order must remain:

```text
build config from raw
resolve_zigzag_enabled_in_place
resolve_volume_enabled_in_place
validate_trade_filter
resolve_trade_filter_mode_in_place
resolve_exit_off_mode_in_place
resolve_exit_b_immediate_off_in_place
resolve_time_filter_in_place
resolve_volume_baseline_session_in_place
resolve_volume_defaults_in_place
```

Add only what is needed:

```text
resolve_wakeup_defaults_in_place
```

But do not materialize strategy values. It may normalize only structural
defaults that are not strategy parameters. Numeric defaults remain `None`.

For Mode D, `resolve_exit_off_mode_in_place` must not silently replace missing
`exit_off_mode` with `"exit A"` before validation. The raw-key guard in
validation is the source of truth.

---

## 7. Global stats

Extend `ZigZagGlobalStats` with defaulted fields:

```python
wakeup_entry_candidate_height_threshold: Optional[float] = None
wakeup_no_fresh_candidate_height_threshold: Optional[float] = None
```

For Mode D:

```text
candidate_trigger_threshold is materialized via existing explicit numeric path
global_median remains materialized and finite for common diagnostics compatibility
Mode D entry does not use candidate_trigger_threshold or global_median
```

This is an explicit Phase 0 limitation: Mode D still requires enough confirmed
ZigZag structure to materialize the common ZigZag diagnostics contract.

Wakeup thresholds:

```text
if entry.candidate_height.enabled:
  wakeup_entry_candidate_height_threshold =
    np.quantile(confirmed_heights_pct, q, method="linear")

if exit.no_fresh_candidate.enabled:
  wakeup_no_fresh_candidate_height_threshold =
    np.quantile(confirmed_heights_pct, q, method="linear")
```

Min-leg guard:

```text
if Mode D and any enabled wakeup component requires quantile:
  required_legs = max(zigzag.local_window, 10)
  if n_legs_total < required_legs:
    ConfigError
```

Legacy modes must not run wakeup quantile checks.

---

## 8. Data plumbing

### 8.1 Required raw arrays

Mode D may need raw `high`, `low`, and `volume`.

Current state:

- `run_backtest_fast` already receives OHLC;
- `apply()` receives `close`, but not raw `high`, `low`, `volume`;
- tester has `df["volume"]`, but old volume filter passes only `VolumeRuntime`.

Add trailing optional parameters only:

```text
run_period:
  reads df["volume"] only if wakeup volume is enabled

run_single_backtest(..., volume: Optional[np.ndarray] = None)

run_backtest_fast(..., volume: Optional[np.ndarray] = None)

zigzag_st_filter.apply(...,
                       high: Optional[np.ndarray] = None,
                       low: Optional[np.ndarray] = None,
                       volume: Optional[np.ndarray] = None)
```

Do not insert parameters before existing optional parameters.

### 8.2 Call-site compatibility

All existing WF/optimizer call-sites must remain valid without passing
`volume`. Since Mode D is rejected by `wf_grid`, WF runtime does not need to
provide raw volume in Phase 0.

Tester path must pass raw volume when:

```text
mode D and wakeup_regime.entry.volume_expansion.enabled == true
```

### 8.3 Early-exit truncation

If `volume` is passed and `early_exit` truncates arrays, any derived wakeup
diagnostic arrays must already be inside `filter_diagnostics` and therefore
truncated synchronously with existing diagnostics.

Raw `volume` itself does not need to be returned.

---

## 9. Data validation

Add helper:

```text
is_wakeup_volume_enabled(trade_filter_config) -> bool
```

Update tester data validation so volume column is required when either:

```text
trade_filter.volume.enabled == true
or
mode D + wakeup_regime.entry.volume_expansion.enabled == true
```

Validation:

```text
volume column exists
volume numeric
no NaN
no Inf
volume >= 0
```

Mode D ATR validation:

```text
high/low/close lengths match
high/low/close finite
high/low/close > 0
high >= low
```

These runtime checks apply only when corresponding wakeup component is enabled.
Disabled components must not require unavailable arrays.

---

## 10. Runtime metrics

Add pure helpers in `zigzag_st_filter.py`:

```text
_compute_wakeup_atr_ratio(high, low, close, short_window, long_window)
  -> np.ndarray float64

_compute_wakeup_volume_ratio(volume, short_window, baseline_window)
  -> np.ndarray float64
```

ATR:

- use existing `calculate_true_range` and `calculate_atr_rma`;
- ratio = short ATR / long ATR;
- bars before `long_window - 1` are NaN;
- if `n < long_window`, return all-NaN;
- non-finite ratio means `atr_ok = false`;
- no unhandled `ValueError` for short data.

Volume:

- independent of `trade_filter.volume` and `VolumeRuntime`;
- Phase 0 uses rolling mean;
- ratio = short rolling mean / baseline rolling mean;
- bars before `baseline_window - 1` are NaN;
- baseline `<= 0` means `volume_ok = false`;
- non-finite ratio means `volume_ok = false`.

---

## 11. Mode D entry logic

Evaluate entry only when:

```text
state_at_bar_start == OFF
not combined_reset_event[t]
time_filter_in_window[t] == true
```

Entry condition is AND of enabled components:

```text
candidate_height_ok:
  disabled -> true
  enabled  -> finite candidate_height_pct[t]
              and candidate_height_pct[t] >= wakeup_entry_candidate_height_threshold

candidate_age_ok:
  disabled -> true
  enabled  -> candidate_age_bars[t] > 0
              and candidate_age_bars[t] <= max_bars

candidate_direction_ok:
  candidate_leg_direction[t] in {-1, +1}

trade_mode_ok:
  _trade_mode_allows_direction(candidate_leg_direction[t], trade_mode)

atr_ok:
  disabled -> true
  enabled  -> finite atr_ratio[t] and atr_ratio[t] >= min_ratio

volume_ok:
  disabled -> true
  enabled  -> finite volume_ratio[t] and volume_ratio[t] >= min_ratio
```

If all entry checks pass:

```text
state = ST_ACTIVE_FREEZE
held_pos = candidate_leg_direction[t]
filtered_positions[t + 1] = held_pos
trade_filter_trigger_source[t] = "wakeup_regime"
filter_allowed_entry[t] = 1
wakeup_starts_count increments via summary
wakeup_cycle_age_bars[t] = 0
wakeup_bars_since_fresh_candidate[t] = 0
```

Mode D must never enter:

```text
WAIT_FIRST_ST_FLIP
ST_ACTIVE_MONITORING
ST_COUNTING_ZZ_LEGS
```

Phase 0 active state for Mode D is `ST_ACTIVE_FREEZE`. `ST_STOPPING` is used
only by `block_new_entries` after Exit C.

---

## 12. Fresh candidate semantics

A candidate is fresh for wakeup exit logic when:

```text
candidate_leg_direction[t] == active_cycle_direction
candidate_age_bars[t] > 0
candidate_age_bars[t] <= no_fresh_candidate.max_age_bars
candidate_height_pct[t] is finite
candidate_height_pct[t] >= wakeup_no_fresh_candidate_height_threshold
```

If `no_fresh_candidate` is disabled, this condition is not evaluated and never
fires.

Counters:

```text
wakeup_cycle_age_bars:
  trigger decision bar = 0
  next bar = 1
  increments while wakeup active or stopping
  -1 when OFF

wakeup_bars_since_fresh_candidate:
  0 on trigger bar if fresh
  1 on trigger bar if not fresh but entry opened
  resets to 0 whenever fresh candidate appears during active cycle
  increments otherwise while active or stopping
  -1 when OFF
```

Boundary tests for trigger-bar fresh vs not-fresh are required.

---

## 13. Exit C conditions

Exit C condition priority:

```text
ttl > no_fresh_candidate
```

TTL:

```text
enabled and wakeup_cycle_age_bars[t] >= ttl.bars
```

No fresh:

```text
enabled and wakeup_bars_since_fresh_candidate[t] >= timeout_bars
```

Exit C is evaluated only while Mode D cycle is active:

```text
ST_ACTIVE_FREEZE
ST_STOPPING for block_new_entries counters only
```

Exit C trigger arrays fire only on the first decision bar where the condition
is reached. They must not keep firing every bar in `ST_STOPPING`.

Bar priority:

```text
combined_reset > opposite_st_flip > exit_c
```

---

## 14. Exit actions

### 14.1 Shared open-to-open contract

All Mode D position changes are decision-at-close `t`, execution-at-open
`t + 1`.

Diagnostics for the decision must be written on decision bar `t`.
This is required because trade-level diagnostics derive the exit decision
from `exit_index - 1`.

### 14.2 block_new_entries

On Exit C decision bar `t` with open position:

```text
wakeup_exit_ttl_triggered[t] or wakeup_exit_no_fresh_candidate_triggered[t] = 1
wakeup_exit_reason[t] = "ttl" or "no_fresh_candidate"
wakeup_exit_close_triggered[t] = 0
state = ST_STOPPING
filtered_positions[t + 1] = current position
new entries/reverses are blocked
counters continue growing in ST_STOPPING
```

In `ST_STOPPING`:

```text
same-direction ST flip -> no effect
opposite ST flip decision bar t -> close at open t+1
wakeup_exit_reason[t] = "opposite_st_flip"
filtered_positions[t + 1] = 0
state = OFF
counters reset after the close decision
no reversal opens on that bar
```

### 14.3 close_position

On Exit C decision bar `t` with open position:

```text
wakeup_exit_ttl_triggered[t] or wakeup_exit_no_fresh_candidate_triggered[t] = 1
wakeup_exit_reason[t] = "ttl" or "no_fresh_candidate"
wakeup_exit_close_triggered[t] = 1
filtered_positions[t + 1] = 0
state = OFF after decision
counters reset after decision
ST_STOPPING is not entered
opposite ST flip is not awaited
```

`wakeup_exit_close_triggered` is intentionally on decision bar `t`, not on
execution bar `t + 1`.

After close, a new wakeup window may open only on a later qualifying decision
bar. No same-bar close-and-reopen is allowed.

---

## 15. Reset and time filter

`combined_reset_event = daily_reset_event | time_filter_reset_event`.

On reset decision bar `t`:

```text
if Mode D cycle is active:
  wakeup_exit_reason[t] = "reset"
  filtered_positions[t + 1] = 0
state = OFF
counters = -1 after decision
trigger_source[t] remains "none"
no new wakeup entry on reset bar
```

Existing reset behavior for legacy modes must remain unchanged.

---

## 16. Opposite ST flip

Mode D uses SuperTrend flip only as an exit signal after wakeup has started.
It is not an entry prerequisite.

While `ST_ACTIVE_FREEZE`:

```text
opposite flip -> close at open t+1, state OFF, reason opposite_st_flip
same-direction flip -> no effect
```

While `ST_STOPPING`:

```text
opposite flip -> close at open t+1, state OFF, reason opposite_st_flip
same-direction flip -> no effect
```

No reversal position is opened by opposite flip in Mode D.

---

## 17. Wakeup diagnostics

Add Mode D arrays:

```text
wakeup_regime_active                        int8
wakeup_entry_all_ok                         int8
wakeup_entry_candidate_height_ok            int8
wakeup_entry_candidate_age_ok               int8
wakeup_entry_candidate_direction_ok         int8
wakeup_entry_trade_mode_ok                  int8
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
wakeup_exit_close_triggered                 int8
wakeup_exit_action_mode                     object
wakeup_exit_reason                          object
```

Sentinels:

```text
inactive int arrays: 0, except counters = -1
inactive float arrays: NaN
wakeup_exit_action_mode: constant config echo
wakeup_exit_reason: "none"
```

Allowed `wakeup_exit_reason` values:

```text
none
ttl
no_fresh_candidate
reset
opposite_st_flip
```

All arrays length `n`. Dtypes must be pinned in tests.

---

## 18. Summary

### 18.1 Tester summary

Make `testing.runner._build_filter_diagnostics_summary` mode-aware.

For Mode D add top-level fields:

```text
zigzag_mode = "D"
exit_off_mode = "exit C"
wakeup_enabled = true
wakeup_exit_action_mode
wakeup_starts_count
wakeup_entry_attempts_count
wakeup_exit_ttl_count
wakeup_exit_no_fresh_candidate_count
wakeup_exit_close_count
wakeup_exit_reset_count
wakeup_exit_opposite_st_flip_count
wakeup_bars_active
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
```

Legacy counters may remain present but must not be used to describe wakeup
behavior. In Mode D:

```text
trigger_count_candidate_threshold = 0
trigger_count_confirmed_median = 0
trigger_count_both = 0
median_stop_triggered_count = 0
zz_leg_stop_triggered_count = 0
```

### 18.2 wf_grid summary

`wf_grid` runtime rejects Mode D, so no wakeup fields are added to WF exports
in Phase 0.

However, reject tests must verify that schema accepts wakeup keys far enough
to produce the intended shared-validator error instead of local unknown-key
failure.

---

## 19. Excel export

### 19.1 FilterDiagnostics_100

Add display names for all wakeup arrays.

Existing legacy display names must not change.

### 19.2 filters_summary

Add Mode D parameter rows and per-period counters listed in section 18.

For non-D modes, output must remain byte/schema compatible except for
append-only display-name maps that do not affect disabled exports.

### 19.3 ZigZag_Trigger_Events

Wakeup trigger rows are selected by existing rule:

```text
trade_filter_trigger_source[t] != "none"
```

But Mode D requires explicit branch behavior:

```text
if trigger_source == "wakeup_regime":
  Triggered Lifecycle Start = true when state_at_t is ST_ACTIVE_FREEZE
  Threshold Used = wakeup_entry_candidate_height_threshold
  Quantile Used = wakeup_entry_candidate_height_quantile
  Candidate Height % = wakeup_entry_candidate_height_value
  Candidate Age Bars = wakeup_entry_candidate_age_bars
  Candidate Leg Direction = wakeup_entry_candidate_leg_direction
  Immediate Candidate Entry Used = 0
  Immediate Candidate Entry Block Reason = "mode_not_c"
```

Linked Trade ID must support wakeup open-to-open entry. Do not rely only on
legacy WAIT/FREEZE scanning assumptions.

---

## 20. Signal events and trade diagnostics

### 20.1 Signal events

`build_signal_events` must not fail on:

```text
filter_trigger_source == "wakeup_regime"
```

Because wakeup entries are not ST-flip signals, signal events may not contain
a one-to-one row for every wakeup entry. This is acceptable in Phase 0 if:

- export does not crash;
- filter columns remain stable for ST signal rows;
- wakeup starts are visible in `ZigZag_Trigger_Events` and diagnostics sheets.

### 20.2 Trade-level diagnostics

Update `attach_trade_filter_diagnostics`.

Required exit reasons:

```text
wakeup_exit_ttl
wakeup_exit_no_fresh_candidate
wakeup_exit_reset
wakeup_exit_opposite_st_flip
```

Mapping rule:

```text
exit_signal_idx = exit_index - 1
read wakeup_exit_reason[exit_signal_idx]
if wakeup_exit_reason != "none":
  map to wakeup-specific exit_reason
else:
  preserve existing legacy mapping
```

For `block_new_entries`, opposite flip may either keep the existing
`filter_stopping_opposite_flip` or use `wakeup_exit_opposite_st_flip`, but the
choice must be documented and tested. Preferred Phase 0 behavior:

```text
wakeup_exit_opposite_st_flip
```

---

## 21. Backtest/result contracts

`run_backtest_fast` and `run_single_backtest` must remain backward-compatible.

Result invariants:

```text
len(equity_curve) == len(positions) == len(trend)
len(equity_curve) == len(returns) + 1
all filter_diagnostics arrays length == len(positions)
```

Add dtype checks for wakeup arrays either in tests or in `BacktestResult`
construction. If added to runtime, use `ConfigError` for wakeup dtype drift,
matching recent strict diagnostics behavior.

---

## 22. Implementation order

Expected effort: 3-5 weeks for one engineer with tests and export verification.
The main risk is not entry logic, but preserving diagnostics/export contracts.

### Phase 0a - safe vertical slice

1. Capture green baseline for relevant donor and wf_grid tests.
2. Add config dataclasses/parser/`__all__`.
3. Update donor allowed-key schema.
4. Update wf_grid local strict schema.
5. Add validation/caller gate for Mode D, Exit C, wakeup_regime.
6. Add config reject/accept tests, including wf_grid intended rejects.
7. Extend `ZigZagGlobalStats` with wakeup thresholds.
8. Add global-stats tests for wakeup quantiles and min-leg guard.
9. Add `is_wakeup_volume_enabled` and data validation.
10. Add trailing `volume` plumbing through tester/engine/backtest/apply.
11. Extract common apply setup/allocation/finalize helpers.
12. Add ATR/volume ratio helpers and tests.
13. Implement Mode D entry only, with open-to-open positions.
14. Add Mode D entry/FSM tests.

### Phase 0b - lifecycle and diagnostics

15. Add wakeup counters and fresh-candidate logic.
16. Implement TTL and no_fresh Exit C conditions.
17. Implement `block_new_entries`.
18. Implement `close_position` with decision-bar diagnostics.
19. Implement reset and opposite ST flip semantics.
20. Add dtype/keyset tests for Mode D diagnostics.
21. Make tester summary mode-aware.
22. Add Excel display names and `filters_summary` wakeup rows.
23. Add explicit wakeup branch in `ZigZag_Trigger_Events`.
24. Update trade diagnostics exit_reason mapping.
25. Add integration export tests for both action modes.
26. Run legacy regression tests.
27. Run single-config tester acceptance for both action modes.

---

## 23. Required tests

### 23.1 Config/schema

```text
tester accepts D + exit C + wakeup_regime.enabled=true
tester rejects D without raw exit_off_mode="exit C"
tester rejects D without wakeup_regime.enabled=true
tester rejects D + candidate_trigger_threshold="auto"
tester rejects D + candidate_trigger_quantile
tester rejects exit C with mode != D
tester rejects wakeup_regime with mode != D
tester rejects exit C + exit_off_zz_leg_count
tester rejects exit C + exit_b_immediate_off
tester rejects exit C + legacy triggers block
wf_grid rejects Mode D via shared-validator path
wf_grid rejects exit C via shared-validator path
wf_grid rejects wakeup_regime via shared-validator path
unknown key in every wakeup sub-block is rejected
legacy modes A/B/C/A+B/C+B still validate as before
```

### 23.2 Wakeup fields

```text
enabled non-bool rejected
quantile outside (0,1) rejected
windows/bars < 1 rejected
min_ratio <= 0 rejected
atr long_window < short_window rejected
volume baseline_window < short_window rejected
action.mode block_new_entries accepted
action.mode close_position accepted
unknown action.mode rejected
enabled component requires numeric parameters
disabled component may omit numeric parameters
no enabled entry component rejected
no enabled exit condition rejected
```

### 23.3 Data plumbing

```text
old run_backtest_fast calls without volume still work
old run_single_backtest calls without volume still work
Mode D + volume_expansion enabled + missing volume column rejected early
Mode D + volume_expansion disabled + missing volume allowed
volume NaN/Inf/negative rejected when wakeup volume enabled
high/low required only when ATR expansion enabled
```

### 23.4 Runtime metrics

```text
ATR ratio uses true range + RMA
n < long_window returns all NaN, no unhandled exception
ATR warmup blocks until long_window - 1
ATR NaN/Inf -> atr_ok false
volume warmup -> volume_ok false
volume baseline zero -> volume_ok false
volume NaN/Inf -> volume_ok false
disabled entry component passes without data
```

### 23.5 Entry

```text
candidate_height gates entry
candidate_age gates entry
candidate_direction gates entry
trade_mode gates direction
ATR gates entry
volume gates entry
enabled components combine as AND
candidate_direction == 0 blocks
combined_reset bar blocks
time_filter outside window blocks
legacy candidate_trigger_threshold does not gate Mode D entry
legacy VolumeRuntime does not gate Mode D entry
```

### 23.6 FSM

```text
Mode D enters directly from OFF
Mode D never enters WAIT_FIRST_ST_FLIP
Mode D never enters ST_ACTIVE_MONITORING
Mode D never enters ST_COUNTING_ZZ_LEGS
UP candidate opens long
DOWN candidate opens short
position appears at t+1
same-direction ST flip has no effect
opposite ST flip closes without reversal
legacy loop is not executed for Mode D
```

### 23.7 Exit C

```text
trigger bar age = 0
next bar age = 1
fresh on trigger bar -> bars_since_fresh = 0
not fresh on trigger bar -> bars_since_fresh = 1
TTL fires at age >= bars
no_fresh fires at bars_since_fresh >= timeout_bars
priority ttl > no_fresh_candidate
bar priority reset > opposite_st_flip > exit_c
Exit C arrays fire only once
disabled exit condition never fires
```

### 23.8 block_new_entries

```text
Exit C with position -> ST_STOPPING
position is held after Exit C
ST_STOPPING blocks new entries and reverses
counters continue in ST_STOPPING
opposite ST flip closes at t+1
opposite ST flip does not reverse
exit reason is wakeup_exit_opposite_st_flip
```

### 23.9 close_position

```text
Exit C with position -> close at t+1
wakeup_exit_close_triggered == 1 on decision bar t
state is OFF after decision
counters reset to -1 after decision
ST_STOPPING is not reached
opposite ST flip is not awaited
new window cannot open on same decision bar
new window can open on later qualifying bar
ttl and no_fresh both close correctly
trade exit_reason maps from decision bar t
```

### 23.10 Reset

```text
combined_reset closes active wakeup cycle
reset writes wakeup_exit_reason="reset" only for active cycle
reset clears counters to -1
reset bar does not open new wakeup cycle
legacy reset behavior unchanged
```

### 23.11 Diagnostics/export

```text
Mode D includes full common keyset
Mode D includes all wakeup fields
all arrays length n
dtypes match contract
wakeup_exit_action_mode equals config
wakeup_starts_count == count(trigger_source == "wakeup_regime")
wakeup_bars_active == sum(wakeup_regime_active)
wakeup_exit_close_count == sum(wakeup_exit_close_triggered)
FilterDiagnostics_100 contains wakeup display names
filters_summary contains wakeup counters and config echo
ZigZag_Trigger_Events includes wakeup rows
wakeup trigger rows link to trades
signal_events does not crash on wakeup
trade exit_reason maps for ttl/no_fresh/reset/opposite flip
full Excel export passes for both action modes
legacy exports unchanged
```

### 23.12 Regression

```text
Modes A/B/C/A+B/C+B unchanged
exit A unchanged
exit B unchanged
exit_b_immediate_off unchanged
time_filter unchanged
daily_reset unchanged
standalone volume unchanged
zigzag + old volume gate unchanged
wf_grid reject tests pass
single-config tester acceptance passes for both action modes
```

---

## 24. Verification commands

Use explicit donor path when running tester-related tests.

Core regression:

```text
python -m pytest wf_grid/tests/test_wp2_config_trade_filter.py -q
python -m pytest wf_grid/tests/test_wp3_zigzag_global_stats.py -q
python -m pytest wf_grid/tests/test_wp4_zigzag_per_bar.py -q
python -m pytest wf_grid/tests/test_wp5_zigzag_fsm.py -q
python -m pytest wf_grid/tests/test_wp7_backtest_integration.py -q
python -m pytest wf_grid/tests/test_pr5_schema_contract.py -q
python -m pytest wf_grid/tests/test_pr6_excel_contract.py -q
```

New wakeup tests:

```text
python -m pytest donor/tests -q
```

Acceptance:

```text
run_tester_single_config_mode.bat
```

Run acceptance twice:

```text
wakeup_regime.exit.action.mode = block_new_entries
wakeup_regime.exit.action.mode = close_position
```

Acceptance checks:

```text
wakeup_starts_count > 0
positions follow candidate_leg_direction
no WAIT_FIRST_ST_FLIP in Mode D
Exit C applies configured action.mode
opposite ST flip closes without reversal
Excel export does not crash
ZigZag_Trigger_Events has wakeup rows
trade exit_reason is populated for wakeup exits
legacy modes still pass regression
wf_grid rejects Phase 0 config
```

---

## 25. Main risks and mitigations

| Risk | Mitigation |
|---|---|
| Mode D diagnostics diverge from common contract | Shared allocation/finalize helpers; keyset/dtype tests |
| Legacy A/B/C regression | Keep legacy transition logic unchanged; characterize before refactor |
| `wf_grid` rejects wakeup as unknown key before shared validator | Update `wf_grid` local strict schema and test intended reject path |
| `close_position` off-by-one | Decision-bar diagnostics; trade diagnostics test using `exit_index - 1` |
| Excel trigger rows are present but semantically wrong | Explicit `wakeup_regime` branch in `ZigZag_Trigger_Events` |
| Summary counters describe ST flips instead of wakeup starts | Mode-aware summary counters |
| Missing raw volume not caught early | `is_wakeup_volume_enabled` and data validator update |
| `VolumeRuntime` accidentally gates Mode D | Separate wakeup volume helper; tests with old volume gate disabled/enabled |
| ATR/volume helpers fail on short data | all-NaN fail-closed behavior; warmup tests |
| Same-bar close and reopen | Explicit no same-bar reopen rule; tests |
| Trade exit_reason remains `st_flip` for wakeup exits | Wakeup mapping in `attach_trade_filter_diagnostics` |
| Scope creep into WF runtime | Caller gate rejects Mode D in `wf_grid`; no WF export wakeup fields |

---

## 26. Definition of Done

```text
Mode D config is accepted only by tester.
wf_grid rejects Mode D / exit C / wakeup_regime through intended validation path.
Mode D starts from OFF by candidate_leg_direction without waiting for ST flip.
Mode D does not execute legacy FSM transition loop.
Exit C supports block_new_entries and close_position.
close_position writes decision-bar diagnostics and closes at t+1.
Reset/time_filter/opposite ST flip are deterministic and tested.
Diagnostics include full common keyset plus wakeup fields.
Summary and Excel are mode-aware.
Trade-level exit_reason is correct for wakeup exits.
Legacy modes and old volume behavior pass regression tests.
Single-config tester produces meaningful wakeup counters and non-empty wakeup trigger rows.
```

---

## 27. Deferred

```text
structural_compression exit
WF Grid runtime Mode D
WF Grid wakeup export
WF OOS Mode D
parallel/transport changes for Mode D
force_flat/global flatten
hard-close with immediate reversal
production effective-config tracking
```
