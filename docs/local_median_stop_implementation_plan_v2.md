# Implementation Plan v2: Mode D Exit C `local_median_stop`

## 1. Цель

Добавить tester-only Exit C условие `local_median_stop` для Mode D
`wakeup_regime`.

Условие завершает активный wakeup-cycle, когда на confirmed leg bar:

```text
local_median_N[t] < global_median
```

Фича использует существующие данные:

- `local_median_N` и `local_median_available` из `ZigZagPerBar`;
- `global_median` из `ZigZagGlobalStats`;
- существующий `trade_filter.zigzag.local_window`.

Новые параметры окна, quantile, timeout, freshness-age или отдельный источник
медиан не добавляются.

## 2. Границы и архитектурное решение

Фича является tester-only на продуктовой поверхности, но часть конфиг-контракта и
runtime-кода живёт в shared пакете `donor/supertrend_optimizer`.

### 2.1. Что меняем

```text
donor/supertrend_optimizer/core/trade_filter_config.py
donor/supertrend_optimizer/core/zigzag_st_filter.py
donor/supertrend_optimizer/core/filter_trade_diagnostics.py
donor/supertrend_optimizer/testing/runner.py
donor/supertrend_optimizer/io/excel_tester.py
wf_grid/config/loader.py
```

Тесты:

```text
wf_grid/tests/test_wp2_config_trade_filter.py
wf_grid/tests/test_wakeup_mode_d_entry.py
wf_grid/tests/test_pr5_schema_contract.py
wf_grid/tests/test_pr6_excel_contract.py
wf_grid/tests/test_xlsx_export.py
donor/tests/core/test_wakeup_config_dataclasses.py
donor TESTER/tests/test_wp_t2_load_tester_config.py
donor TESTER/tests/test_phase2_tester_diagnostics_dtype_contract.py
donor TESTER/tests/test_phase2_wp_t7_excel_export.py
donor TESTER/tests/test_xlsx_cycle_sheet.py
donor TESTER/tests/test_phase2_tester_global_stats_init_failure.py
```

### 2.2. Почему `wf_grid/config/loader.py` обязательно меняем

`wf_grid` остаётся без поддержки `wakeup_regime`, Mode D и Exit C. Однако в
текущем коде есть parity-контракт: `wf_grid.config.loader._ALLOWED_KEYS` должен
совпадать с shared `TRADE_FILTER_ALLOWED_KEYS` для wakeup paths.

Это решение имеет приоритет над формулировкой ТЗ, где сказано не расширять
`wf_grid` whitelist. По фактическому коду источник истины здесь -
`wf_grid/tests/test_wp2_config_trade_filter.py::test_wakeup_allowed_key_paths_match_shared`.
Если shared whitelist расширить, а `wf_grid` whitelist нет, сборка падает.

Поэтому:

- в `wf_grid/config/loader.py` добавляем только schema whitelist для
  `local_median_stop`, чтобы не сломать drift-test;
- runtime/export `wf_grid/export/*` не меняем;
- `wf_grid` YAML с `wakeup_regime.exit.local_median_stop` всё равно должен
  падать через shared validator с `wakeup_regime_unsupported_pipeline`
  при `caller_pipeline="wf_grid"`.

Это сохраняет tester-only поведение и одновременно уважает текущий контракт
архитектуры.

## 3. Не-цели

- Не менять legacy median-stop / Exit A.
- Не переиспользовать Exit A fail-closed семантику.
- Не добавлять новые параметры окна, quantile или timeout.
- Не менять `wf_grid/export/*`.
- Не добавлять поддержку Mode D / `wakeup_regime` в production `wf_grid`.
- Не считать reason counters и action counters взаимоисключающими.
- Не строить runtime-тесты на случайной OHLC ZigZag динамике, когда нужен
  deterministic predicate test.

## 4. Контракт конфигурации

Новый YAML shape:

```yaml
trade_filter:
  enabled: true
  type: zigzag_st_mode
  zigzag:
    enabled: true
    mode: D
    reversal_threshold: 0.01
    local_window: 5
    candidate_trigger_threshold: 0.05
  lifecycle:
    freeze_confirmed_legs: 0
    exit_off_mode: "exit C"
  wakeup_regime:
    enabled: true
    entry:
      candidate_height:
        enabled: true
        quantile: 0.65
    exit:
      ttl:
        enabled: false
      no_fresh_candidate:
        enabled: false
      max_trades_per_cycle:
        enabled: false
      local_median_stop:
        enabled: true
      action:
        mode: block_new_entries
```

`local_median_stop` can be the only enabled Exit C condition. This does not
weaken any other Mode D requirement:

- `wakeup_regime.enabled` must be strict bool;
- at least one wakeup entry component must be enabled;
- `wakeup_regime.exit.action.mode` remains required when wakeup is enabled;
- Mode D / Exit C cross-field requirements remain required;
- `candidate_trigger_threshold` and global stats requirements remain required;
- `wf_grid` still rejects the whole `wakeup_regime` block through the
  caller-pipeline gate.

## 5. Work Package 1: Config Contract

Files:

```text
donor/supertrend_optimizer/core/trade_filter_config.py
wf_grid/config/loader.py
donor/tests/core/test_wakeup_config_dataclasses.py
wf_grid/tests/test_wp2_config_trade_filter.py
donor TESTER/tests/test_wp_t2_load_tester_config.py
```

Implementation:

1. Add dataclass:

```python
@dataclass
class TradeFilterWakeupLocalMedianStopExitConfig:
    enabled: object = False
```

2. Extend `TradeFilterWakeupExitConfig`:

```python
local_median_stop: TradeFilterWakeupLocalMedianStopExitConfig = field(
    default_factory=TradeFilterWakeupLocalMedianStopExitConfig
)
```

3. Parse raw block in `build_trade_filter_config_from_raw()`:

```python
local_median_stop_raw = exit_raw.get("local_median_stop") or {}
```

and pass it into `TradeFilterWakeupExitConfig`.

4. Extend `TRADE_FILTER_ALLOWED_KEYS`:

```text
trade_filter.wakeup_regime.exit:
  local_median_stop

trade_filter.wakeup_regime.exit.local_median_stop:
  enabled
```

5. Extend `wf_grid/config/loader.py` `_ALLOWED_KEYS` with the same keys to keep
   whitelist parity. Do not add any `wf_grid` runtime support.

6. Extend the hardcoded `wakeup_paths` list in
   `wf_grid/tests/test_wp2_config_trade_filter.py` with:

```text
trade_filter.wakeup_regime.exit.local_median_stop
```

This makes parity cover both the new parent key under `...exit` and the new
child path `...exit.local_median_stop`.

7. In `_validate_wakeup_regime_block()`:

- validate `local_median_stop.enabled` through `_validate_wakeup_component_enabled`;
- require strict bool when the block is present;
- include `local_median_stop_enabled` in the "at least one enabled exit
  condition" rule;
- keep `action.mode` mandatory for enabled wakeup;
- keep all Mode D entry and cross-field rules unchanged.

Config tests:

- build config with `local_median_stop: {enabled: true}`;
- reject `enabled: "yes"`;
- reject unknown nested key under `local_median_stop`;
- reject malformed mapping shapes through the unknown-key phase:
  `local_median_stop: true`, `local_median_stop: []`;
- accept config where `local_median_stop` is the only enabled exit condition and
  all required Mode D fields are present;
- `wf_grid` config containing `wakeup_regime.exit.local_median_stop` still
  fails with `wakeup_regime_unsupported_pipeline`;
- whitelist parity test remains green.

## 6. Work Package 2: Reason and Action Mapping

Files:

```text
donor/supertrend_optimizer/core/zigzag_st_filter.py
donor/supertrend_optimizer/core/filter_trade_diagnostics.py
```

Use a minimal additive mapping change. Do not introduce a broad string-mapping
refactor in this feature. The runtime change is small, and expanding it into a
cross-module refactor increases the chance of changing existing trade-level
semantics.

Required runtime change:

```python
if reason == "local_median_stop":
    return "exit_local_median_stop"
```

Required trade-level changes:

```python
"local_median_stop": "wakeup_exit_local_median_stop"
"exit_local_median_stop": "wakeup_exit_local_median_stop"
```

Important: extend existing mappings in place. Do not replace them with a
narrower Exit C-only dict.
`reset`, `opposite_st_flip`, `reverse_on_st_flip`, and
`flat_on_disallowed_st_flip` are current behavior and must remain mapped.

`_wakeup_exit_action_for_reason()` must keep raising `AssertionError` for
unknown reasons.

If a central mapping refactor is still desired, do it as a separate
behavior-preserving task before or after this feature, with tests proving all
old mappings are byte-for-byte equivalent.

Mapping tests:

- old `ttl`, `no_fresh_candidate`, `cycle_trade_limit` mappings unchanged;
- `local_median_stop -> exit_local_median_stop`;
- trade-level `local_median_stop -> wakeup_exit_local_median_stop`;
- reset/opposite-ST-flip trade-level mappings unchanged;
- position-action fallback mappings unchanged.

## 7. Work Package 3: Runtime Predicate

File:

```text
donor/supertrend_optimizer/core/zigzag_st_filter.py
```

Implementation:

1. Extend `_WakeupExitConfigParts` with `local_median_stop`.
2. Return `exit_cfg.local_median_stop` from `_resolve_mode_d_wakeup_exit()`.
3. Unpack it beside `wakeup_ttl_cfg`, `wakeup_no_fresh_cfg`, and
   `wakeup_max_trades_per_cycle_cfg`.
4. Compute a runtime boolean:

```text
local_median_stop_enabled =
    mode_d_enabled
    and wakeup_regime.enabled is True
    and local_median_stop_cfg is not None
    and local_median_stop_cfg.enabled is True
```

5. Add the predicate between `no_fresh_candidate` and `cycle_trade_limit`.

Trigger requires all conditions:

```text
wakeup_state_active
state_at_bar_start == ST_ACTIVE_FREEZE
not wakeup_exit_c_fired
not is_reset
local_median_stop_enabled
confirmed is True
bool(local_median_available[t]) is True
isfinite(local_median_N[t])
isfinite(global_median)
local_median_N[t] < global_median
```

Implementation detail: do not use `local_median_available[t] is True`. The
array element is a numpy bool scalar, so identity comparison with Python `True`
can silently evaluate false. Use `bool(local_median_available[t])`.

`isfinite(global_median)` is defensive. Current runtime already validates
global stats, but the guard is acceptable and should fail open.

### 7.1. NaN Policy

`local_median_stop` is fail-open:

- unavailable local median: no trigger;
- NaN/Inf local median: no trigger;
- NaN/Inf global median: no trigger.

Do not reuse legacy Exit A median-stop fail-closed logic.

### 7.2. Event Ordering

Current Mode D ordering must remain explicit:

1. bar-start snapshots;
2. reset wipe;
3. candidate/confirmed primitive evaluation;
4. Mode D wakeup entry from OFF;
5. Exit C reason chain;
6. internal ST flip / position-freeze logic;
7. cycle trade count update;
8. diagnostics writeback.

`local_median_stop` runs in step 5. Therefore, on a bar where confirmed LMS and
internal ST flip both occur, winning LMS suppresses same-bar internal ST flip
via existing `wakeup_exit_c_triggered_this_bar` gating.

Priority chain:

```text
ttl > no_fresh_candidate > local_median_stop > cycle_trade_limit
```

Only the winning branch sets its `*_triggered` flag. Satisfied-but-losing
conditions remain `0`.

The diagnostic array write must be under `if diag_enabled`, matching the
neighboring TTL/no-fresh/cycle-limit branches:

```python
if diag_enabled:
    wakeup_exit_local_median_stop_triggered_arr[t] = np.int8(1)
wakeup_exit_c_reason_this_bar = "local_median_stop"
```

Reason/action state changes remain outside the diagnostics gate, so lite mode
and diagnostic mode produce identical positions.

### 7.3. Action Semantics

Use existing Exit C action semantics:

```text
block_new_entries -> ST_STOPPING, position remains open
close_position    -> OFF, held_pos = 0, cycle runtime reset
```

`wakeup_position_action` on the winning bar:

```text
exit_local_median_stop
```

## 8. Work Package 4: Runtime Diagnostics Contract

Files:

```text
donor/supertrend_optimizer/core/zigzag_st_filter.py
wf_grid/tests/test_wakeup_mode_d_entry.py
donor TESTER/tests/test_phase2_tester_diagnostics_dtype_contract.py
```

Add array allocation:

```text
wakeup_exit_local_median_stop_triggered_arr: np.zeros(n, dtype=np.int8)
```

Add it to the Mode D diagnostics gate only:

```text
wakeup_exit_local_median_stop_triggered
```

Dtype:

```text
np.int8
```

Values:

```text
1 only on the winning LMS bar
0 otherwise
```

Do not add this key to the non-D strict `EXPECTED_KEYSET`. That keyset remains
unchanged.

Mode D keyset strategy:

- extend the existing Mode D wakeup diagnostics subset tests in
  `wf_grid/tests/test_wakeup_mode_d_entry.py`;
- introduce a strict Mode D keyset contract in this task. Build it as:

```python
MODE_D_WAKEUP_EXPECTED_KEYS = frozenset({...})
```

and verify it separately from the non-D keyset. The strict contract must include
`wakeup_exit_local_median_stop_triggered`.

Expected reason/action token sets must include:

```text
wakeup_exit_reason:
  local_median_stop

wakeup_position_action:
  exit_local_median_stop
```

## 9. Work Package 5: Trade-Level Diagnostics

File:

```text
donor/supertrend_optimizer/core/filter_trade_diagnostics.py
```

Expected enriched trade values:

```text
exit_reason = "wakeup_exit_local_median_stop"
wakeup_cycle_exit_reason = "local_median_stop"
wakeup_position_action = "exit_local_median_stop"
```

Extend `_wakeup_exit_reason_at()` in place:

```text
local_median_stop -> wakeup_exit_local_median_stop
```

Update `_wakeup_position_action_at()` only by extending the existing superset,
not by narrowing it:

```text
exit_local_median_stop -> wakeup_exit_local_median_stop
```

Trade-level tests:

- direct enrichment test for raw reason `local_median_stop`;
- direct enrichment test for position action `exit_local_median_stop`;
- reset/opposite-ST-flip mappings still green;
- internal ST flip position-action mappings still green.

## 10. Work Package 6: Tester Summary and Excel Export

Files:

```text
donor/supertrend_optimizer/testing/runner.py
donor/supertrend_optimizer/io/excel_tester.py
wf_grid/tests/test_pr5_schema_contract.py
wf_grid/tests/test_pr6_excel_contract.py
wf_grid/tests/test_xlsx_export.py
donor TESTER/tests/test_phase2_wp_t7_excel_export.py
donor TESTER/tests/test_xlsx_cycle_sheet.py
```

### 10.1. Runner Counter

Add:

```python
"wakeup_exit_local_median_stop_count": _sum_int8(
    "wakeup_exit_local_median_stop_triggered"
)
```

Counter invariant:

- `wakeup_exit_local_median_stop_count` counts raw reason LMS;
- `wakeup_exit_close_count` counts action-mode close;
- if LMS fires with `action.mode == "close_position"`, both counters may
  increment on the same bar.

Do not write tests that sum Exit C reason counters and action counters as if
they were mutually exclusive.

### 10.2. Cycle-Trade-Limit Summary Policy

Keep the existing policy unless explicitly changing it in a separate task:

- add LMS counter because this feature requires it;
- do not add `wakeup_exit_cycle_trade_limit_count` here;
- preserve the existing test that asserts cycle-trade-limit has no new summary
  counter.

This is intentionally asymmetric but documented. If the product decision later
is "all Exit C reasons get summary counters", that should be a separate
contract migration.

### 10.3. Excel Display Names

Add display name:

```text
wakeup_exit_local_median_stop_triggered
  -> Wakeup Exit Local Median Stop Triggered
```

Add summary row:

```text
Wakeup Exit Local Median Stop
```

Place it near existing wakeup exit rows:

```text
Wakeup Exit TTL
Wakeup Exit No Fresh Candidate
Wakeup Exit Local Median Stop
Wakeup Exit Close
```

Update all strict display-name/header snapshots that mirror
`FILTER_DIAGNOSTICS_100_DISPLAY_NAMES`.

Before editing snapshots, run a grep inventory and update every explicit
contract it finds:

```powershell
rg -n "FILTER_DIAGNOSTICS_100_DISPLAY_NAMES|Wakeup Exit TTL|Wakeup Exit No Fresh|wakeup_exit_.*_triggered|wakeup_exit_close_count" donor "donor TESTER" wf_grid
```

Expected inventory includes at least:

```text
wf_grid/tests/test_pr5_schema_contract.py
wf_grid/tests/test_pr6_excel_contract.py
wf_grid/tests/test_xlsx_export.py
wf_grid/tests/test_wakeup_mode_d_entry.py
donor TESTER/tests/test_phase2_wp_t7_excel_export.py
donor TESTER/tests/test_xlsx_cycle_sheet.py
```

## 11. Work Package 7: Global Stats Regression

Files:

```text
wf_grid/tests/test_wp3_zigzag_global_stats.py
donor TESTER/tests/test_phase2_tester_global_stats_init_failure.py
```

Goal: prove LMS-only exit does not require no-fresh quantile or no-fresh global
threshold.

Important clarification:

`local_median_stop`-only does not mean "no global stats requirements". Runtime
still requires:

- finite `global_median`;
- finite `candidate_trigger_threshold`;
- valid Mode D entry configuration;
- enough data for existing global-stats initialization.

Test shape:

- Mode D enabled;
- valid entry component enabled;
- numeric `candidate_trigger_threshold` present;
- `ttl`, `no_fresh_candidate`, `max_trades_per_cycle` disabled;
- `local_median_stop.enabled == True`;
- global stats initialization succeeds;
- `wakeup_no_fresh_candidate_height_threshold` remains `None`.

## 12. Work Package 8: Deterministic Runtime Test Harness

Use direct calls to:

```python
apply(..., per_bar=ZigZagPerBar(...))
```

Build explicit `ZigZagPerBar` arrays:

```text
confirm_event
local_median_N
local_median_available
candidate_height_pct
candidate_age_bars
candidate_leg_direction
```

Use explicit `trend` arrays for ST flip cases.

Do not rely on random or emergent ZigZag behavior from synthetic OHLC when the
test is about the LMS predicate.

Minimum runtime cases:

- LMS + `block_new_entries` -> `ST_STOPPING`, position not closed immediately;
- LMS + `close_position` -> `OFF`, position closed;
- NaN local median -> no trigger;
- unavailable local median -> no trigger;
- unconfirmed bar -> no trigger;
- entry/start bar -> no trigger when `state_at_bar_start == OFF`;
- reset bar -> no trigger;
- LMS independent from `wakeup_bars_since_fresh`;
- LMS independent from no-fresh `quantile/max_age_bars/timeout_bars`;
- TTL + LMS -> TTL wins, LMS flag `0`;
- no-fresh + LMS -> no-fresh wins, LMS flag `0`;
- LMS + cycle-trade-limit -> LMS wins, cycle flag `0`;
- LMS + same-bar internal ST flip -> LMS suppresses same-bar flip action;
- `collect_filter_diagnostics=False` produces same positions and no diagnostics.

Lite-mode regression should mirror the existing cycle-trade-limit lite test:
diagnostic run and lite run must produce identical positions, and lite run must
not allocate diagnostics arrays.

## 13. Backward Compatibility

Must remain true:

- tester configs without `local_median_stop` behave as before;
- non-D diagnostics strict keyset remains unchanged;
- old TTL/no-fresh/cycle-limit tests remain green;
- `wf_grid` YAML with `wakeup_regime.exit.local_median_stop` fails with
  `wakeup_regime_unsupported_pipeline`;
- old `wf_grid` configs pass without new unknown-key errors;
- Exit A median-stop behavior is unchanged;
- reset/opposite-ST-flip trade-level reasons are unchanged;
- cycle-trade-limit summary policy is unchanged.

## 14. Implementation Order

1. Config contract and whitelist parity:
   dataclass, parser, shared whitelist, `wf_grid` whitelist parity, validator,
   config tests, including the new `wakeup_paths` child path.
2. Minimal reason/action mapping:
   add only the LMS branches to existing runtime and trade-level mappings.
3. Runtime predicate:
   config unpacking, diagnostics array, Exit C priority branch, action semantics.
4. Runtime tests:
   deterministic direct-call cases and lite-mode parity.
5. Trade-level diagnostics:
   enriched trade `exit_reason` and action mapping tests.
6. Runner summary:
   LMS counter and counter-invariant test.
7. Excel export:
   display name, summary row, header/snapshot updates.
8. Global stats regression:
   LMS-only exit independent from no-fresh quantile.
9. Full targeted regression sweep and cleanup.

This order keeps schema admission ahead of runtime behavior, and isolates string
contract changes before Excel/snapshot updates.

## 15. Effort and Scope Estimate

Expected implementation size:

- Source changes: about 6 files.
- Test changes: about 10-12 files across `wf_grid`, `donor`, and `donor TESTER`.
- Runtime logic: small, roughly one config field plus one Exit C `elif`.
- Contract/export work: the largest and riskiest part.

Rough effort:

```text
WP1 config + whitelist parity:        small/medium
WP2-WP5 runtime + trade diagnostics:  small
WP6 Excel/snapshots/contracts:        medium/high
WP7 global stats regression:          small/medium
Final regression and cleanup:         medium
```

Most time should be reserved for strict keyset, Excel header, workbook snapshot,
and duplicated `donor` / `donor TESTER` contract updates. The coding change is
not the schedule driver; contract churn is.

## 16. Verification Commands

Targeted:

```powershell
python -m pytest wf_grid/tests/test_wp2_config_trade_filter.py -q
python -m pytest donor/tests/core/test_wakeup_config_dataclasses.py -q
python -m pytest wf_grid/tests/test_wakeup_mode_d_entry.py -q
python -m pytest wf_grid/tests/test_wp3_zigzag_global_stats.py -q
python -m pytest "donor TESTER/tests/test_phase2_tester_diagnostics_dtype_contract.py" -q
python -m pytest "donor TESTER/tests/test_phase2_wp_t7_excel_export.py" -q
python -m pytest "donor TESTER/tests/test_xlsx_cycle_sheet.py" -q
python -m pytest wf_grid/tests/test_pr5_schema_contract.py wf_grid/tests/test_pr6_excel_contract.py wf_grid/tests/test_xlsx_export.py -q
```

Final smoke:

```powershell
python -m pytest wf_grid/tests -m "not slow" -q
```

If donor tester imports require path bootstrap, use the existing
`donor TESTER/tests/conftest.py` setup. Do not copy runtime code into
`donor TESTER/supertrend_optimizer`.

## 17. Acceptance Checklist

- `local_median_stop.enabled: true` parses into config.
- `enabled` is strict bool.
- Unknown and malformed nested keys are rejected.
- `local_median_stop` can be the only enabled Exit C condition.
- Required Mode D entry/action/global-stats fields remain required.
- `wf_grid` whitelist parity remains green.
- `wakeup_paths` parity list includes `trade_filter.wakeup_regime.exit.local_median_stop`.
- `wf_grid` still rejects `wakeup_regime` through caller-pipeline gate.
- Runtime trigger fires only in active Mode D wakeup-cycle on confirmed bar.
- Runtime trigger does not fire on the entry/start bar where `state_at_bar_start == OFF`.
- NaN/unavailable medians fail open.
- Predicate uses `bool(local_median_available[t])`, not identity comparison.
- Predicate uses existing `local_median_N` and `global_median`.
- Priority is `ttl > no_fresh_candidate > local_median_stop > cycle_trade_limit`.
- Only the winning Exit C reason sets its triggered flag.
- The triggered-array write is guarded by `if diag_enabled`.
- Same-bar LMS suppresses internal ST flip action.
- `block_new_entries` and `close_position` use existing action semantics.
- New diagnostic key is Mode-D-only and `np.int8`.
- Non-D diagnostics keyset remains unchanged.
- Strict Mode-D diagnostics keyset is introduced and contains the new key.
- Trade-level `exit_reason` is `wakeup_exit_local_median_stop`.
- Reset/opposite-ST-flip mappings remain unchanged.
- Runner exposes `wakeup_exit_local_median_stop_count`.
- Close counter and LMS counter may both increment on close-position LMS.
- Excel export has the new diagnostics column and summary row.
- `test_pr6_excel_contract.py` is included in the Excel/header inventory.
- Cycle-trade-limit summary policy remains unchanged.
- Old tester and `wf_grid` configs remain backward-compatible.
- Exit A median-stop behavior is unchanged.

## 18. Main Risks

- Forgetting `wf_grid/config/loader.py` whitelist parity and breaking
  `test_wakeup_allowed_key_paths_match_shared`.
- Forgetting to add `trade_filter.wakeup_regime.exit.local_median_stop` to the
  hardcoded `wakeup_paths` parity list.
- Narrowing existing trade-level mappings while adding LMS.
- Introducing a broad reason/action refactor and accidentally changing reset,
  opposite-ST-flip, or internal-ST-flip trade reasons.
- Accidentally adding the new diagnostics key to non-D keysets.
- Failing to introduce the strict Mode-D diagnostics keyset.
- Missing strict Excel/header snapshot tests in `donor TESTER` or `test_pr6`.
- Miscounting reason counters and action counters as mutually exclusive.
- Writing `wakeup_exit_local_median_stop_triggered_arr` outside `if diag_enabled`.
- Using numpy identity comparison such as `local_median_available[t] is True`.
- Changing same-bar ordering around confirmed bar, LMS, ST flip, and
  cycle-trade-limit.
- Building tests from emergent OHLC ZigZag behavior instead of explicit
  `ZigZagPerBar` fixtures.
