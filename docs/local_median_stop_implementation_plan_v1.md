# Implementation Plan v1: Mode D Exit C `local_median_stop`

## 1. Цель

Добавить tester-only Exit C-триггер `local_median_stop` для Mode D
`wakeup_regime`.

Триггер завершает активный wakeup-cycle, когда на confirmed leg bar:

```text
local_median_N[t] < global_median
```

Фича использует существующие `local_median_N` из `ZigZagPerBar` и
`global_median` из `ZigZagGlobalStats`. Новых параметров окна, новых quantile и
нового freshness-timeout не добавлять.

## 2. Не-цели

- Не менять legacy median-stop / Exit A.
- Не менять non-D diagnostics strict keyset в
  `donor TESTER/tests/test_phase2_tester_diagnostics_dtype_contract.py`.
- Не добавлять поддержку `wakeup_regime` в `wf_grid`.
- Не расширять `wf_grid/config/loader.py` whitelist под
  `local_median_stop`.
- Не менять `wf_grid/export/*`.
- Не считать Exit C summary counters взаимоисключающими.
- Не строить runtime-тесты на случайной ZigZag-динамике из OHLC, если нужен
  deterministic predicate test.

## 3. Архитектурные якоря

Активный shared/runtime пакет:

```text
donor/supertrend_optimizer/core/trade_filter_config.py
donor/supertrend_optimizer/core/zigzag_st_filter.py
donor/supertrend_optimizer/core/filter_trade_diagnostics.py
donor/supertrend_optimizer/testing/runner.py
donor/supertrend_optimizer/io/excel_tester.py
```

Tester-контракты и CLI/config tests:

```text
donor TESTER/tests/test_phase2_tester_diagnostics_dtype_contract.py
donor TESTER/tests/test_phase2_wp_t7_excel_export.py
donor TESTER/tests/test_wp_t2_load_tester_config.py
donor TESTER/tests/test_xlsx_cycle_sheet.py
```

WF Grid regression/contract tests, которые могут увидеть shared config/runtime:

```text
wf_grid/tests/test_wp2_config_trade_filter.py
wf_grid/tests/test_wakeup_mode_d_entry.py
wf_grid/tests/test_pr5_schema_contract.py
wf_grid/tests/test_xlsx_export.py
wf_grid/tests/test_wp3_zigzag_global_stats.py
```

Ключевой принцип: config dataclass и shared validator общие, но
`caller_pipeline="wf_grid"` должен продолжать reject-ить весь
`wakeup_regime` через существующий pipeline gate.

## 4. Work Package 1: Config Contract

Файл:

```text
donor/supertrend_optimizer/core/trade_filter_config.py
```

Изменения:

1. Добавить dataclass:

```python
@dataclass
class TradeFilterWakeupLocalMedianStopExitConfig:
    enabled: object = False
```

2. Расширить `TradeFilterWakeupExitConfig`:

```python
local_median_stop: TradeFilterWakeupLocalMedianStopExitConfig = field(
    default_factory=TradeFilterWakeupLocalMedianStopExitConfig
)
```

3. В `build_trade_filter_config_from_raw()` распарсить:

```python
local_median_stop_raw = exit_raw.get("local_median_stop") or {}
```

4. В `TRADE_FILTER_ALLOWED_KEYS` добавить:

```text
trade_filter.wakeup_regime.exit:
  local_median_stop

trade_filter.wakeup_regime.exit.local_median_stop:
  enabled
```

5. В `_validate_wakeup_regime_block()`:

- `local_median_stop.enabled` должен быть strict `bool`;
- unknown keys внутри `local_median_stop` должны отвергаться existing strict
  schema механизмом;
- `local_median_stop.enabled=True` считается enabled exit condition в правиле
  "requires at least one enabled exit condition";
- `exit.action.mode` остается обязательным при включенном `wakeup_regime`, даже
  если единственный enabled exit condition - `local_median_stop`;
- обязательные Mode D entry-поля не ослаблять.

Не менять:

```text
wf_grid/config/loader.py
wf_grid/export/*
```

Тесты WP1:

- load config with `local_median_stop: {enabled: true}`;
- reject `enabled: "yes"`;
- reject unknown nested key;
- accept config where `local_median_stop` is the only enabled exit condition,
  while required Mode D entry/action/candidate threshold fields are present;
- wf_grid config containing `wakeup_regime.exit.local_median_stop` still fails
  with `wakeup_regime_unsupported_pipeline`.

## 5. Work Package 2: Central Reason Mapping

Файлы:

```text
donor/supertrend_optimizer/core/zigzag_st_filter.py
donor/supertrend_optimizer/core/filter_trade_diagnostics.py
```

Перед runtime-логикой завести единый источник строк для Exit C reason/action:

```python
WAKEUP_EXIT_C_REASON_TO_POSITION_ACTION = {
    "ttl": "exit_ttl",
    "no_fresh_candidate": "exit_no_fresh_candidate",
    "local_median_stop": "exit_local_median_stop",
    "cycle_trade_limit": "exit_cycle_trade_limit",
}

WAKEUP_EXIT_C_REASON_TO_TRADE_EXIT_REASON = {
    "ttl": "wakeup_exit_ttl",
    "no_fresh_candidate": "wakeup_exit_no_fresh_candidate",
    "local_median_stop": "wakeup_exit_local_median_stop",
    "cycle_trade_limit": "wakeup_exit_cycle_trade_limit",
}
```

Можно держать mapping в `zigzag_st_filter.py` и локально повторно импортировать
в `filter_trade_diagnostics.py`, либо вынести в маленький shared module внутри
`donor/supertrend_optimizer/core/`. Выбрать минимальный вариант с наименьшим
риском циклических импортов.

`_wakeup_exit_action_for_reason()` должен использовать mapping и продолжать
падать `AssertionError` для неизвестной причины.

Тесты WP2:

- existing TTL/no_fresh/cycle_trade_limit reason mappings не меняют поведение;
- `local_median_stop -> exit_local_median_stop`;
- trade-level `local_median_stop -> wakeup_exit_local_median_stop`.

## 6. Work Package 3: Runtime Predicate

Файл:

```text
donor/supertrend_optimizer/core/zigzag_st_filter.py
```

Изменения:

1. Расширить `_WakeupExitConfigParts` полем `local_median_stop`.
2. В `_resolve_mode_d_wakeup_exit()` вернуть `exit_cfg.local_median_stop`.
3. Распаковать config рядом с `wakeup_ttl_cfg`,
   `wakeup_no_fresh_cfg`, `wakeup_cycle_trade_limit_cfg`.
4. Добавить diagnostic array:

```text
wakeup_exit_local_median_stop_triggered_arr: np.zeros(n, dtype=np.int8)
```

5. Добавить его в `filter_diagnostics` только внутри существующего
   `zigzag_mode == "D"` / wakeup diagnostics gate.
6. В Exit C chain вставить predicate между `no_fresh_candidate` и
   `cycle_trade_limit`.

Predicate должен требовать одновременно:

```text
wakeup_state_active
state_at_bar_start == ST_ACTIVE_FREEZE
not wakeup_exit_c_fired
not is_reset
local_median_stop.enabled is True
confirmed[t] is True
local_median_available[t] is True
isfinite(local_median_N[t])
isfinite(global_median)
local_median_N[t] < global_median
```

Если `global_median` в текущем runtime уже гарантирован validator/global-stats
инвариантами, отдельная fail-open проверка `isfinite(global_median)` всё равно
допустима как defensive guard.

Priority chain:

```text
ttl > no_fresh_candidate > local_median_stop > cycle_trade_limit
```

Только победивший branch ставит свой `*_triggered` флаг. На баре, где TTL или
no_fresh победили LMS, `wakeup_exit_local_median_stop_triggered == 0`.

Action semantics использовать существующие:

```text
block_new_entries -> ST_STOPPING, позиция сразу не закрывается
close_position    -> OFF, held_pos = 0, close counter/action-mode logic как сейчас
```

Тесты WP3 строить на direct-call fixture с явным `ZigZagPerBar`, где заданы
`confirm_event`, `local_median_N`, `local_median_available`, `trend`/ST flips и
candidate primitives.

Минимальные runtime cases:

- LMS + `block_new_entries` -> `ST_STOPPING`, позиция не закрыта сразу;
- LMS + `close_position` -> `OFF`, позиция закрыта;
- NaN local median -> no trigger;
- unavailable local median -> no trigger;
- unconfirmed bar -> no trigger;
- reset bar -> no trigger;
- LMS независим от `wakeup_bars_since_fresh`;
- LMS независим от `quantile/max_age_bars/timeout_bars`;
- TTL + LMS -> TTL wins, LMS flag 0;
- no_fresh + LMS -> no_fresh wins, LMS flag 0;
- LMS + cycle_trade_limit -> LMS wins, cycle flag 0.

## 7. Work Package 4: Diagnostics Contract

Файлы:

```text
donor/supertrend_optimizer/core/zigzag_st_filter.py
donor TESTER/tests/test_phase2_tester_diagnostics_dtype_contract.py
wf_grid/tests/test_wakeup_mode_d_entry.py
```

Сохранить non-D `EXPECTED_KEYSET` без изменений.

Добавить отдельный Mode-D keyset contract:

```python
BASE_EXPECTED_KEYSET = EXPECTED_KEYSET
MODE_D_WAKEUP_EXPECTED_KEYS = frozenset({
    "wakeup_regime_active",
    "wakeup_cycle_age_bars",
    "wakeup_bars_since_fresh_candidate",
    "wakeup_exit_reason",
    "wakeup_exit_ttl_triggered",
    "wakeup_exit_no_fresh_candidate_triggered",
    "wakeup_exit_local_median_stop_triggered",
    "wakeup_exit_cycle_trade_limit_triggered",
    "wakeup_exit_close_triggered",
    "wakeup_active_direction",
    "wakeup_position_action",
    ...
})
```

Если в `wf_grid/tests/test_wakeup_mode_d_entry.py` уже есть Mode-D dtype/key
проверки, расширить их новым ключом. Важно: это не должно означать поддержку
фичи в `wf_grid` pipeline; эти тесты используют shared runtime helpers.

Dtype ожидания:

```text
wakeup_exit_local_median_stop_triggered: np.int8
```

Value contract:

```text
1 только на winning LMS bar
0 иначе
```

## 8. Work Package 5: Trade-Level Diagnostics

Файл:

```text
donor/supertrend_optimizer/core/filter_trade_diagnostics.py
```

Изменения:

- `_wakeup_exit_reason_at()` должен маппить
  `local_median_stop -> wakeup_exit_local_median_stop`;
- `_wakeup_position_action_at()` может маппить
  `exit_local_median_stop -> wakeup_exit_local_median_stop` для
  консистентности отчета.

Ожидаемые значения в enriched trades:

```text
exit_reason = "wakeup_exit_local_median_stop"
wakeup_cycle_exit_reason = "local_median_stop"
wakeup_position_action = "exit_local_median_stop"
```

Тесты WP5:

- direct enrichment test для `wakeup_exit_reason`;
- direct enrichment test для `wakeup_position_action`;
- old TTL/no_fresh/cycle mappings остаются зелеными.

## 9. Work Package 6: Tester Counters and Excel Export

Файлы:

```text
donor/supertrend_optimizer/testing/runner.py
donor/supertrend_optimizer/io/excel_tester.py
wf_grid/tests/test_pr5_schema_contract.py
wf_grid/tests/test_xlsx_export.py
donor TESTER/tests/test_phase2_wp_t7_excel_export.py
donor TESTER/tests/test_xlsx_cycle_sheet.py
```

`testing/runner.py`:

```python
"wakeup_exit_local_median_stop_count": _sum_int8(
    "wakeup_exit_local_median_stop_triggered"
)
```

`io/excel_tester.py` header map:

```text
wakeup_exit_local_median_stop_triggered
  -> Wakeup Exit Local Median Stop Triggered
```

Summary row рядом с текущими wakeup exit rows:

```text
Wakeup Exit Local Median Stop
```

Counter invariant:

```text
wakeup_exit_close_count counts action-mode close_position
wakeup_exit_local_median_stop_count counts raw reason local_median_stop
```

При LMS + `close_position` оба счетчика могут увеличиться на одном баре. Не
писать тесты, которые суммируют Exit C reason counters как mutually exclusive с
action-mode counters.

Grep-driven contract migration перед правкой тестов:

```powershell
rg -n "wakeup_exit_ttl_triggered|wakeup_exit_no_fresh_candidate_triggered|Wakeup Exit TTL|Wakeup Exit No Fresh|wakeup_exit_close_count|FILTER_DIAGNOSTICS_100_DISPLAY_NAMES" donor "donor TESTER" wf_grid
```

Обновить все явные header/display-name ожидания, но не добавлять LMS в
`wf_grid/export/*`.

## 10. Work Package 7: Global Stats / Local-Median-Only Regression

Файлы:

```text
wf_grid/tests/test_wp3_zigzag_global_stats.py
donor TESTER/tests/test_phase2_tester_global_stats_init_failure.py
```

Добавить минимальный synthetic dataset/config, где:

- Mode D включен;
- entry component валиден;
- `candidate_trigger_threshold` задан числом;
- all old exit conditions disabled;
- `local_median_stop.enabled=True`;
- global stats успешно инициализируются.

Цель теста: доказать, что LMS-only exit не требует no_fresh quantile и не
ломает global stats initialization.

## 11. Work Package 8: Backward Compatibility

Проверить регрессии:

- старые tester configs без `local_median_stop` дают прежнее поведение;
- non-D diagnostics strict keyset не изменился;
- old Mode D TTL/no_fresh/cycle_limit tests зеленые;
- `wf_grid` YAML с `wakeup_regime.exit.local_median_stop` получает
  `wakeup_regime_unsupported_pipeline`;
- старые wf_grid configs проходят без новых unknown-key ошибок;
- exit A median-stop поведение не изменилось.

## 12. Рекомендуемый порядок реализации

1. WP1: config dataclass/parser/validator + config tests.
2. WP2: reason/action/trade-exit mapping refactor без изменения поведения.
3. WP3: runtime arrays, Mode-D diagnostics key, Exit C predicate.
4. WP5: trade-level diagnostics.
5. WP6: runner counters and Excel display names/summary.
6. WP4: strict Mode-D diagnostics keyset contract после появления runtime key.
7. WP7: global stats LMS-only regression.
8. WP8: full regression sweep and cleanup.

Такой порядок уменьшает риск: сначала schema admission, потом строковые
контракты, затем поведение, и только после этого export/snapshots.

## 13. Проверочные команды

Локальные targeted tests после соответствующих WP:

```powershell
python -m pytest wf_grid/tests/test_wp2_config_trade_filter.py -q
python -m pytest wf_grid/tests/test_wakeup_mode_d_entry.py -q
python -m pytest wf_grid/tests/test_wp3_zigzag_global_stats.py -q
python -m pytest "donor TESTER/tests/test_phase2_tester_diagnostics_dtype_contract.py" -q
python -m pytest "donor TESTER/tests/test_phase2_wp_t7_excel_export.py" -q
python -m pytest wf_grid/tests/test_pr5_schema_contract.py wf_grid/tests/test_xlsx_export.py -q
```

Финальный smoke:

```powershell
python -m pytest wf_grid/tests -m "not slow" -q
```

Если targeted tester tests импортируют активный пакет иначе, перед запуском
проверить `PYTHONPATH`/bootstrap в существующих `donor TESTER/tests/conftest.py`
и не переносить runtime-код в `donor TESTER/supertrend_optimizer`.

## 14. Acceptance Checklist

- `local_median_stop.enabled:true` парсится и валидируется.
- `enabled` strict bool; unknown nested keys rejected.
- LMS can be the only enabled exit condition, while required Mode D fields stay
  required.
- Runtime trigger fires only in Mode D active wakeup-cycle on confirmed bar.
- NaN/unavailable local median fail-open.
- Predicate uses existing `local_median_N` and `global_median`.
- Priority is `ttl > no_fresh_candidate > local_median_stop > cycle_trade_limit`.
- Only winning Exit C reason sets its triggered flag.
- `block_new_entries` and `close_position` respect existing action semantics.
- New diagnostic key is Mode-D-only and `np.int8`.
- Non-D `EXPECTED_KEYSET` remains unchanged.
- Trade-level `exit_reason` is `wakeup_exit_local_median_stop`.
- Runner exposes `wakeup_exit_local_median_stop_count`.
- Excel export has column and summary row.
- Close counter and LMS counter are allowed to both increment on close-position
  LMS.
- `wf_grid` still rejects wakeup_regime via caller_pipeline gate.
- Old tester/wf_grid configs remain backward-compatible.
