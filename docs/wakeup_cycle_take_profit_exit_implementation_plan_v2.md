# Implementation Plan v2: Wakeup Cycle Take Profit Exit

## 1. Цель

Добавить tester-only Exit C `cycle_take_profit` для `trade_filter.wakeup_regime`
в ZigZag Mode D.

Exit завершает текущий wakeup-cycle, когда сумма realized net PnL закрытых
сделок, принадлежащих активному wakeup-cycle, достигает заданного порога.
Открытая позиция не участвует в расчете threshold.

Целевой YAML:

```yaml
trade_filter:
  wakeup_regime:
    exit:
      cycle_take_profit:
        enabled: true
        pnl_pct: 0.01
      action:
        mode: close_position
```

`pnl_pct` задается как fraction:

```text
0.01  = +1.0%
0.005 = +0.5%
0.025 = +2.5%
```

Runtime accumulator хранится в integer micro-percent, а диагностика показывает
значение в fraction-единицах.

## 2. Границы

Фича включается только в tester pipeline. `wf_grid` продолжает отклонять любые
`wakeup_regime` конфиги через существующий gate
`wakeup_regime_unsupported_pipeline`.

В `wf_grid` разрешены только parity/dead-code правки:

- config whitelist должен распознавать новый путь, чтобы ошибка оставалась
  `wakeup_regime_unsupported_pipeline`, а не `unknown key`;
- summary builder может считать новый diagnostics key, если он когда-либо
  присутствует во входном `filter_diagnostics`;
- collector/export columns добавляются только если локальные strict contracts
  требуют сохранения нового summary key.

Не меняем:

- поддержку Mode D в `wf_grid`;
- open-to-open execution contract;
- семантику `wakeup_exit_close_triggered`: флаг означает action requested;
- trade-level delayed attribution для `block_new_entries`;
- поведение configs без `cycle_take_profit` или с `enabled: false`.

## 3. Ключевой контракт точности

`trades_df["net_pnl_pct"]` остается источником истины для trade economics.
Runtime `cycle_take_profit` обязан считать closed-leg PnL тем же production
helper, который использует `extract_trades`.

Runtime unit:

```text
cycle_realized_pnl_micropct: int
```

Конвертация closed-leg net PnL:

```python
def net_pnl_pct_to_micropct(net_pnl_pct: float) -> int:
    return int(round(round(float(net_pnl_pct), 6) * 1_000_000))
```

Важно: внешний `round(..., 6)` повторяет контракт `trades_df`, а внешний
`int(round(...))` защищает от binary-float truncation на значениях вроде
`999999.9999999999`.

Threshold:

```python
threshold_micropct = int(round(float(pnl_pct) * 1e8))
trigger = cycle_realized_pnl_micropct >= threshold_micropct
```

Диагностическое зеркало:

```python
wakeup_cycle_realized_pnl_pct[t] = tested_cycle_pnl_micropct / 1e8
```

`net_pnl_pct` хранится в процентах, поэтому `1.0%` становится
`1_000_000` micro-percent, а diagnostics fraction получается делением на
`1e8`: `1_000_000 / 1e8 == 0.01`.

## 4. Основные файлы

Config:

```text
donor/supertrend_optimizer/core/trade_filter_config.py
wf_grid/config/loader.py
wf_grid/config/schema.py
wf_grid/tests/test_wp2_config_trade_filter.py
donor/tests/core/test_wakeup_config_dataclasses.py
donor TESTER/tests/test_wp_t2_load_tester_config.py
```

Trade economics:

```text
donor/supertrend_optimizer/core/trades.py
donor/tests/core/test_extract_trades_last_bar.py
donor/tests/core/test_invariants_pnl.py
```

Runtime:

```text
donor/supertrend_optimizer/core/zigzag_st_filter.py
donor/supertrend_optimizer/core/backtest.py
donor/supertrend_optimizer/engine/run.py
wf_grid/tests/test_wakeup_mode_d_entry.py
```

Diagnostics, enrichment, summary, export:

```text
donor/supertrend_optimizer/core/filter_trade_diagnostics.py
donor/supertrend_optimizer/testing/runner.py
donor/supertrend_optimizer/io/excel_tester.py
wf_grid/wf/step_executor.py
wf_grid/collect/step_collector.py
wf_grid/tests/test_pr5_schema_contract.py
wf_grid/tests/test_pr6_excel_contract.py
wf_grid/tests/test_xlsx_export.py
donor TESTER/tests/test_phase2_tester_diagnostics_dtype_contract.py
donor TESTER/tests/test_phase2_wp_t7_excel_export.py
donor TESTER/tests/test_xlsx_cycle_sheet.py
```

## 5. Implementation Gates

Реализацию надо вести тремя проверяемыми gates, чтобы ошибки economics helper
не смешивались с runtime FSM ошибками.

Gate A: config + trade economics helper.

Gate B: `apply()` plumbing + runtime accumulator + Exit C behavior.

Gate C: diagnostics, trade enrichment, summary, Excel/export contracts.

Нельзя начинать Gate B до зеленых golden tests Gate A.

## 6. Gate A: Config Contract

Добавить dataclass:

```python
@dataclass
class TradeFilterWakeupCycleTakeProfitExitConfig:
    enabled: object = False
    pnl_pct: object = None
```

Расширить `TradeFilterWakeupExitConfig`:

```python
cycle_take_profit: TradeFilterWakeupCycleTakeProfitExitConfig = field(
    default_factory=TradeFilterWakeupCycleTakeProfitExitConfig
)
```

Обязательные config edits:

- `build_trade_filter_config_from_raw()`: читать
  `exit_raw.get("cycle_take_profit") or {}`;
- `TRADE_FILTER_ALLOWED_KEYS`: добавить `cycle_take_profit` под
  `trade_filter.wakeup_regime.exit`;
- `TRADE_FILTER_ALLOWED_KEYS`: добавить child path
  `trade_filter.wakeup_regime.exit.cycle_take_profit` с ключами
  `enabled`, `pnl_pct`;
- `wf_grid/config/loader.py`: повторить whitelist parity;
- `wf_grid/config/schema.py`: re-export нового dataclass;
- `__all__` в config source: добавить новый dataclass.

Validation rules в `_validate_wakeup_regime_block()`:

- `enabled` must be strict bool when block/key present;
- `enabled=True` требует присутствующий `pnl_pct`;
- `pnl_pct` must be numeric, finite, and `> 0`;
- reject `0`, negative values, bool, `"1%"`, `None` when enabled;
- `cycle_take_profit.enabled=True` считается enabled Exit C condition;
- если `wakeup_regime.enabled=False`, валидный nested block принимается и
  является runtime no-op;
- `wf_grid` config с этим block продолжает падать через
  `wakeup_regime_unsupported_pipeline`.

Особый guardrail: обновить ручной список enabled exit conditions. Сейчас он
перечисляет `ttl`, `no_fresh_candidate`, `max_trades_per_cycle`,
`local_median_stop`; в него обязательно добавить `cycle_take_profit`, иначе
валидный конфиг с единственным новым Exit C будет отвергнут.

Config tests:

```text
valid enabled=true pnl_pct in {0.01, 0.005, 0.025}
enabled=false without pnl_pct is accepted
enabled=true without pnl_pct is rejected
pnl_pct invalid values are rejected
enabled invalid values are rejected
unknown nested keys are rejected
cycle_take_profit can be the only enabled Exit C condition
wakeup_regime.enabled=false + valid cycle_take_profit block is accepted
wf_grid rejects as unsupported pipeline, not unknown key
shared/wf_grid whitelist parity remains green
schema re-export and __all__ are updated
```

## 7. Gate A: Trade Economics Helper

Extract one production helper from `extract_trades`:

```python
def closed_leg_trade_economics(
    entry_idx: int,
    exit_idx: int,
    direction: int,
    positions: np.ndarray,
    execution_prices: np.ndarray,
    commission_rate: float,
) -> dict[str, float]:
    ...
```

Return exactly:

```text
gross_pnl_pct
commission_pct
net_pnl_pct
```

All three values are rounded to 6 decimals.

Helper rules:

- `direction`: `+1` for long, `-1` for short;
- gross PnL uses simple entry/exit price return:
  - long: `(exit_price - entry_price) / entry_price * 100`;
  - short: `(entry_price - exit_price) / entry_price * 100`;
- base commission uses `commission_per_bar[entry_idx:exit_idx] * 100`;
- `commission_per_bar[i] = abs(positions[i + 1] - positions[i]) * commission_rate`;
- staged rounding must match current production behavior exactly:
  1. compute and round base `gross_pnl_pct`, base `commission_pct`, and base
     `net_pnl_pct` to 6 decimals, as `_build_trade` currently does;
  2. apply opening-from-flat adjustment to the already-rounded values and round
     `commission_pct` and `net_pnl_pct` again to 6 decimals;
  3. apply reversal split adjustment to the already-rounded values and round
     `commission_pct` and `net_pnl_pct` again to 6 decimals;
- if `entry_idx > 0` and `positions[entry_idx - 1] == 0`, include opening
  commission from flat: `commission_per_bar[entry_idx - 1] * 100`;
- reversal split must preserve current adjacency semantics:
  - opening-side adjustment applies only when this leg opens on a reversal bar;
  - closing-side adjustment applies only when this leg closes into a reversal
    bar;
  - the condition must be equivalent to the current paired post-processing
    check where the closing trade `exit_idx == entry_idx` of the opening trade;
  - do not replace this with a looser sign-only heuristic unless tests prove
    exact equivalence on flat, reversal, pending, and reset scenarios;
- reversal opening side adds one side commission to the opening leg:
  `commission_rate * 100`;
- reversal closing side removes one side commission from the closing leg:
  `commission_rate * 100`;
- preserve pending/open-at-end behavior where `entry_idx == exit_idx`;
- validate execution prices are present, finite, and positive for the two
  referenced indices.

Important: `extract_trades` must use this helper for every trade row. Do not
leave `_build_trade` and a separate commission post-processing path as a second
production source of truth.

Performance note: the helper returns the full economics triplet because
`extract_trades` needs all three values. The FSM currently uses only
`net_pnl_pct`; per-close dict allocation is acceptable for tester-only scope.
Before removing the WF/Grid wakeup gate, revisit this API and consider a lean
tuple/namedtuple or net-only fast path.

Allowed temporary test-only oracle:

- a frozen copy of legacy behavior may live only in tests;
- production code must have exactly one economics implementation.
- golden fixtures must be captured from current production output before the
  helper refactor; do not hand-author expected economics numbers.

Golden tests must be written before the refactor:

```text
flat-to-flat trades_df identical
reversal F-16 split identical
pending/open-at-end identical
daily reset / time reset close identical
commission conservation invariant identical
gross_pnl_pct / commission_pct / net_pnl_pct identical to 6 decimals
optimizer, wf_grid, and tester paths still see identical trades_df economics
optimizer and wf_grid backtest paths produce identical trades_df after helper extraction
```

## 8. Gate B: `apply()` API Plumbing

Extend signature:

```python
apply(..., commission_rate: float | None = None)
```

Runtime rules:

- `commission_rate` required only when `cycle_take_profit.enabled is True`;
- if enabled and `commission_rate is None`, raise clear `ConfigError`,
  `RuntimeError`, or `ValueError` with config path in message;
- if disabled, direct tests may continue passing `commission_rate=None`;
- `open_prices` becomes required only when feature is enabled;
- enabled branch validates `open_prices` is 1-D, length `n`, finite,
  and strictly positive.

Call sites:

- `donor/supertrend_optimizer/core/backtest.py` passes `commission` into
  `_zz_apply(..., commission_rate=commission)`;
- inspect tester runner and engine wrapper paths that may call `apply()`
  indirectly;
- direct unit tests with new feature pass explicit `commission_rate`;
- existing direct unit tests without feature stay compatible.

Contract tests:

- update signature/inspection tests so `commission_rate` is an allowed
  non-artifact argument;
- keep forbidden args rejected conceptually: `returns`, `equity_curve`,
  `trades_df`, `metrics`, `raw_artifacts`.

Tests:

```text
apply() without feature accepts commission_rate=None
enabled=true + commission_rate=None raises clear error
enabled=true + open_prices=None raises clear error
enabled=true + invalid open_prices raises clear error
run_backtest_fast passes commission into _zz_apply
direct tester path can enable the feature without missing commission_rate
```

## 9. Gate B: Runtime State Model

Add loop-local scalars in `apply()`:

```python
cycle_realized_pnl_micropct = 0
tested_cycle_pnl_micropct = 0
trade_entry_idx = -1
trade_entry_pos = 0
trade_entry_in_cycle_scope = False
```

`trade_entry_in_cycle_scope` is mandatory. It defines membership of the open
leg. Current FSM state alone is not enough because forced close and
`ST_STOPPING` delayed close can materialize after the wakeup-cycle is already
inactive.

State table for `trade_entry_in_cycle_scope`:

```text
event                                                scope after event
---------------------------------------------------  -----------------
wakeup opens a real position in ST_ACTIVE_FREEZE     True
active-cycle reversal opens a new leg                True, unless Exit C fires on the same bar
restore/release opens from flat in active cycle       True
materialized flat close while still active            False after accumulation
cycle_take_profit close_position fires                False immediately after trigger
cycle_take_profit block_new_entries fires             False immediately after trigger
daily/time reset fires                                False
opposite ST flip ends cycle                           False
ST_STOPPING delayed close                             False
new wakeup-cycle starts                               reset accumulator; new real entry sets True
```

Same-bar reversal plus `cycle_take_profit` rule:

- the old leg is accumulated at bar start;
- the new reversal leg may receive metadata with `entry_idx=t`;
- if `cycle_take_profit` fires later on the same bar and moves the cycle to
  OFF or `ST_STOPPING`, immediately set `trade_entry_in_cycle_scope=False` for
  that new leg;
- therefore the forced/delayed close of the new leg cannot contribute to the
  threshold that fired.

Entry metadata rules:

- wakeup start that actually opens a position:
  - `trade_entry_idx = t`;
  - `trade_entry_pos = held_pos`;
  - `trade_entry_in_cycle_scope = True`;
- internal reversal:
  - old leg is accumulated first at the bar-start transition;
  - new leg metadata becomes `entry_idx = t`, `entry_pos = new held_pos`;
  - new leg is in scope only if state remains active after the old-leg
    accumulation/reset checks;
- restore from flat on ST flip or position-freeze release:
  - if a real position opens from flat during active wakeup-cycle, set metadata
    to `t`, current position, `True`;
- flat:
  - reset metadata to `-1, 0, False`;
- forced close requested by `close_position`:
  - keep metadata until the materialized transition
    `filtered_positions[t] -> filtered_positions[t + 1]` is observed on the
    next bar;
  - set `trade_entry_in_cycle_scope = False` when cycle exits to OFF so the
    forced-close leg cannot contribute to the threshold that requested it;
- `block_new_entries`:
  - when Exit C moves to `ST_STOPPING`, set `trade_entry_in_cycle_scope = False`;
  - delayed close in `ST_STOPPING` is not counted into the completed cycle.

Reset `cycle_realized_pnl_micropct` with other per-cycle accumulators:

- combined daily/time reset;
- new wakeup trigger;
- Exit C `close_position`;
- Exit C `block_new_entries`;
- `ST_STOPPING -> OFF`;
- opposite ST flip ending the cycle;
- any actual OFF transition in Mode D.

Main invariant:

```text
PnL from one wakeup-cycle must never carry into another wakeup-cycle.
```

## 10. Gate B: Bar Processing Order

The exact Mode D order inside the `for t in range(n)` loop must be:

```text
0. Capture immutable bar-start snapshots.
1. Accumulate materialized close from positions[t - 1] -> positions[t].
2. Store tested_cycle_pnl_micropct for diagnostics/predicate.
3. Apply combined reset if present.
4. Start new wakeup cycle from OFF if entry conditions pass.
5. Evaluate Exit C chain using the tested accumulator for this bar.
6. Write diagnostics snapshot for the value tested on this bar.
7. Apply ST flip / position-freeze / release logic.
8. Decide filtered_positions[t + 1].
9. Persist remaining diagnostics.
```

This differs from the current reset-first layout and is required for reset-bar
correctness.

Materialized close detection at bar start:

```text
t > 0
AND trade_entry_idx >= 0
AND trade_entry_pos != 0
AND filtered_positions[t - 1] != 0
AND filtered_positions[t] != filtered_positions[t - 1]
```

Accumulate only if `trade_entry_in_cycle_scope is True` for that open leg:

```python
leg = closed_leg_trade_economics(
    trade_entry_idx,
    t,
    trade_entry_pos,
    filtered_positions,
    open_prices,
    commission_rate,
)
cycle_realized_pnl_micropct += net_pnl_pct_to_micropct(leg["net_pnl_pct"])
```

After any materialized close:

- if `filtered_positions[t] == 0`, reset trade metadata to flat;
- if reversal happened, install new metadata with `entry_idx = t` and
  `entry_pos = filtered_positions[t]`;
- new reversal leg membership depends on whether the wakeup-cycle remains
  active after the accumulation/reset/Exit C decision for this bar.

Diagnostic snapshot:

```python
tested_cycle_pnl_micropct = cycle_realized_pnl_micropct
```

`wakeup_cycle_realized_pnl_pct[t]` must use `tested_cycle_pnl_micropct`, not
the post-reset value.

Reset-close semantic decision:

- if reset on bar `t` requests a close by writing `filtered_positions[t + 1]=0`,
  that close is still unrealized when the reset decision is made;
- it materializes only as `positions[t] -> positions[t + 1]` and would be
  detectable on the next iteration;
- because the cycle has already been reset, this reset-caused close is excluded
  from the completed cycle's threshold by design;
- add an explicit test so this exclusion is not rediscovered as a bug later.

## 11. Gate B: Final-Bar Contract

`apply()` can only materialize a close when `t + 1 < n`, because positions are
length `n` in the runtime result and decisions write `filtered_positions[t+1]`.

Contract:

- `cycle_take_profit` may set diagnostics/action on final bar;
- if `t + 1 >= n`, no new materialized close is possible inside `apply()`;
- an open position on final bar remains represented as pending/open-at-end in
  `extract_trades`;
- `wakeup_exit_close_triggered=1` still means action requested, not actual close;
- tests must assert this behavior explicitly.

Do not add a special finalization close in this task. That would change the
open-to-open contract and pending trade semantics.

## 12. Gate B: Exit C Predicate

Extend `_WakeupExitConfigParts` with:

```text
cycle_take_profit
```

Extend `_resolve_mode_d_wakeup_exit()` accordingly.

Extend `_wakeup_exit_action_for_reason()`:

```python
if reason == "cycle_take_profit":
    return "exit_cycle_take_profit"
```

Priority chain:

```text
1. cycle_take_profit
2. ttl
3. no_fresh_candidate
4. local_median_stop
5. cycle_trade_limit
```

Predicate:

```python
cycle_take_profit_enabled
and tested_cycle_pnl_micropct >= threshold_micropct
```

Gates:

```text
state_at_bar_start == ST_ACTIVE_FREEZE
state == ST_ACTIVE_FREEZE
not wakeup_exit_c_fired
not is_reset
cycle_take_profit_enabled
```

Action semantics:

`close_position`:

- set `wakeup_exit_reason_this_bar = "cycle_take_profit"`;
- set `wakeup_position_action_this_bar = "exit_cycle_take_profit"`;
- set `wakeup_exit_cycle_take_profit_triggered[t] = 1`;
- set `wakeup_exit_close_triggered[t] = 1`;
- transition cycle to OFF;
- clear per-cycle accumulator after diagnostics snapshot has captured tested
  value;
- set `trade_entry_in_cycle_scope = False`;
- existing position close is materialized through normal
  `filtered_positions[t] -> filtered_positions[t + 1]` flow if `t + 1 < n`.

`block_new_entries`:

- set reason/action as above;
- set `wakeup_exit_cycle_take_profit_triggered[t] = 1`;
- transition to `ST_STOPPING`;
- do not close current position immediately;
- set `trade_entry_in_cycle_scope = False`;
- delayed close attribution remains `filter_stopping_opposite_flip`.

One-shot behavior uses existing `wakeup_exit_c_fired`.

Tie-break tests:

```text
cycle_take_profit + ttl on same bar -> reason == "cycle_take_profit"
cycle_take_profit fires no more than once per wakeup-cycle
existing ttl/no_fresh/local_median/cycle_trade_limit priority remains stable
```

## 13. Gate B: Runtime Acceptance Tests

Use deterministic direct `apply(..., per_bar=ZigZagPerBar(...))` tests for exact
threshold behavior. Avoid emergent synthetic OHLC when the target is exact PnL.

Core tests:

```text
enabled=false preserves old positions
pnl_pct=0.01, closed +0.0099 -> no trigger
pnl_pct=0.01, closed +0.0100 -> trigger
pnl_pct=0.01, closed +0.0120 -> one trigger and cycle -> OFF
unrealized open position does not affect threshold
new wakeup-cycle starts with accumulator 0
close_position sets wakeup_exit_close_triggered=1 and positions go flat when t+1 exists
close_position on final bar sets action-requested diagnostics but does not materialize a new close
block_new_entries does not close current position immediately
cycle_take_profit beats ttl on same bar
trigger flag is one-shot per wakeup-cycle
diagnostic snapshot on trigger bar shows value reaching threshold
collect_filter_diagnostics=False returns None diagnostics
lite and diagnostic positions are identical
reset-bar first accounts already-materialized close, then resets cycle
reset-caused close itself is excluded by design because it was unrealized at reset
time-filter reset follows the same accumulation-before-reset rule
commission_rate=0 works and threshold uses pure gross PnL
early_exit/truncated artifacts keep diagnostics, positions, and trades index-compatible
```

Exact membership tests:

```text
cycle_realized_pnl_micropct
==
sum(net_pnl_pct_to_micropct(trades_df.net_pnl_pct) for member trades)
```

Cover:

- flat-to-flat;
- reversal with F-16 split;
- forced close from `close_position` excluded from the threshold that caused it;
- `ST_STOPPING` delayed close excluded after cycle is inactive;
- final pending/open-at-end behavior.

No float tolerance in membership assertion.

## 14. Gate C: Diagnostics

Add Mode D diagnostics fields:

```text
wakeup_cycle_realized_pnl_pct            float64 fraction
wakeup_exit_cycle_take_profit_triggered  int8
```

Allocation:

- only when `collect_filter_diagnostics=True`;
- follow existing `_allocate_apply_arrays()` style;
- no diagnostic arrays allocated in lite mode.

Finalization:

- add fields only to Mode D diagnostics output;
- non-Mode-D diagnostics keyset remains unchanged;
- Mode D keyset gains exactly two runtime keys for this feature unless snapshot
  tests explicitly include display-only/export-only additions elsewhere.

Snapshot:

```python
wakeup_cycle_realized_pnl_pct[t] = tested_cycle_pnl_micropct / 1e8
```

The trigger bar must show the value that was tested by Exit C, even though the
cycle accumulator is reset after the action.

Lite mode:

```text
collect_filter_diagnostics=False -> filter_diagnostics is None
positions match diagnostic mode bit-for-bit
```

Strict diagnostics tests to update:

- `wf_grid/tests/test_wakeup_mode_d_entry.py` expected Mode D keysets/dtypes;
- `wf_grid/tests/test_pr5_schema_contract.py` display/keyset snapshots where
  they mirror `FILTER_DIAGNOSTICS_100_DISPLAY_NAMES`;
- `donor TESTER/tests/test_phase2_tester_diagnostics_dtype_contract.py` strict
  tester keyset/dtype contract.

## 15. Gate C: Trade-Level Mapping

Extend `filter_trade_diagnostics.py`:

```text
_wakeup_exit_reason_at:
  "cycle_take_profit" -> "wakeup_exit_cycle_take_profit"

_wakeup_position_action_at:
  "exit_cycle_take_profit" -> "wakeup_exit_cycle_take_profit"
```

Expected trade-level behavior:

- `close_position` can produce
  `exit_reason == "wakeup_exit_cycle_take_profit"` when the trade exit signal
  index points to the trigger bar;
- `block_new_entries` keeps delayed close attribution, usually
  `filter_stopping_opposite_flip`;
- do not change enrichment timing in this task.

Tests:

```text
close_position maps to wakeup_exit_cycle_take_profit
block_new_entries does not over-attribute delayed close
old ttl/no_fresh/local_median/cycle_trade_limit mappings remain unchanged
reset/opposite-ST-flip mappings remain unchanged
final-bar requested close does not fabricate a trade-level actual close
```

## 16. Gate C: Summary

Tester summary in `testing/runner.py`:

```python
"wakeup_exit_cycle_take_profit_count": _sum_int8(
    "wakeup_exit_cycle_take_profit_triggered"
)
```

WF/Grid summary in `wf_grid/wf/step_executor.py`:

- count `wakeup_exit_cycle_take_profit_triggered` if key exists in
  `filter_diagnostics`;
- do not infer wakeup semantics from config;
- if key is absent, omit the counter from the summary builder output.

Collector/export parity:

- add `wakeup_exit_cycle_take_profit_count` to
  `wf_grid/collect/step_collector.py::_FILTER_SUMMARY_COLUMNS`;
- when WF/Grid remains gated and the summary key is absent, flattening emits
  `None` for this column, consistent with other missing summary values;
- if a synthetic diagnostics dict contains
  `wakeup_exit_cycle_take_profit_triggered`, `_compute_filter_diagnostics_summary`
  returns the counter and step collector preserves it.

Do not repeat the existing `cycle_trade_limit` hole. For
`cycle_take_profit`, summary counter is required.

Summary tests:

```text
tester summary exposes wakeup_exit_cycle_take_profit_count
WF/Grid summary builder counts key when present
WF/Grid summary builder does not accept wakeup runtime config
step_collector exports/preserves wakeup_exit_cycle_take_profit_count
WF/Grid gated real runs leave the flattened value None
```

## 17. Gate C: Excel And Display Names

Add display names:

```text
wakeup_cycle_realized_pnl_pct
  -> Wakeup Cycle Realized PnL Pct

wakeup_exit_cycle_take_profit_triggered
  -> Wakeup Exit Cycle Take Profit Triggered
```

Add summary row near other wakeup exits:

```text
Wakeup Exit TTL
Wakeup Exit No Fresh Candidate
Wakeup Exit Local Median Stop
Wakeup Exit Cycle Take Profit
Wakeup Exit Close
```

Update strict display/header snapshots wherever they mirror
`FILTER_DIAGNOSTICS_100_DISPLAY_NAMES`, including tester and `wf_grid` tests.

Excel/export tests:

```text
diagnostics sheet contains both new columns when Mode D diagnostics are retained
summary sheet contains Wakeup Exit Cycle Take Profit
strict display/header snapshots updated
non-Mode-D export headers unchanged
disabled feature does not emit misleading trigger counts
```

## 18. Implementation Order

1. Add config dataclass, parser, donor whitelist, validator, `__all__`.
2. Add `wf_grid/config/loader.py` whitelist parity and unsupported-pipeline test.
3. Add `wf_grid/config/schema.py` re-export.
4. Add golden/reference tests for current `trades_df` behavior.
5. Extract `net_pnl_pct_to_micropct()` and `closed_leg_trade_economics()`.
6. Refactor `extract_trades` to use the helper; keep behavior identical.
7. Add `apply(..., commission_rate=None)` and enabled-branch validations.
8. Pass `commission_rate` from backtest/tester runtime paths.
9. Add runtime state scalars, especially `trade_entry_in_cycle_scope`.
10. Insert accumulation-before-reset step at bar start.
11. Add `tested_cycle_pnl_micropct` snapshot path.
12. Add `cycle_take_profit` predicate before TTL.
13. Implement `close_position` and `block_new_entries` action semantics.
14. Add final-bar explicit behavior tests.
15. Add diagnostics arrays and Mode D finalization keys.
16. Add trade-level reason/action mapping.
17. Add tester and WF/Grid summary counters.
18. Update step collector/export parity if required by strict contracts.
19. Add Excel display names, summary row, and strict snapshot updates.
20. Run focused tests after each gate, then nearby regression suite.

## 19. Verification Commands

Gate A config:

```powershell
python -m pytest wf_grid/tests/test_wp2_config_trade_filter.py -q
python -m pytest donor/tests/core/test_wakeup_config_dataclasses.py -q
python -m pytest "donor TESTER/tests/test_wp_t2_load_tester_config.py" -q
```

Gate A trade economics:

```powershell
python -m pytest donor/tests/core/test_extract_trades_last_bar.py donor/tests/core/test_invariants_pnl.py -q
```

Gate B runtime:

```powershell
python -m pytest wf_grid/tests/test_wakeup_mode_d_entry.py -q
python -m pytest wf_grid/tests/test_wp6_st_flip_event_ordering.py wf_grid/tests/test_wp7_backtest_integration.py -q
```

Gate C contracts/export:

```powershell
python -m pytest wf_grid/tests/test_pr5_schema_contract.py wf_grid/tests/test_pr6_excel_contract.py wf_grid/tests/test_xlsx_export.py -q
python -m pytest "donor TESTER/tests/test_phase2_tester_diagnostics_dtype_contract.py" -q
python -m pytest "donor TESTER/tests/test_phase2_wp_t7_excel_export.py" "donor TESTER/tests/test_xlsx_cycle_sheet.py" -q
```

Final non-slow sweep if time allows:

```powershell
python -m pytest wf_grid/tests -m "not slow" -q
```

## 20. Main Risks And Guardrails

Risk: helper does not match old `trades_df` after commission post-processing.

Guardrail: golden fixtures before runtime work; helper becomes the only
production implementation; staged rounding and reversal adjacency semantics
must match the current production code exactly.

Risk: binary float conversion misses exact threshold.

Guardrail: shared `net_pnl_pct_to_micropct()` helper with outer `round()` before
`int`.

Risk: forced close PnL is counted into the threshold that requested it.

Guardrail: accumulation only at next bar materialized transition, plus
`trade_entry_in_cycle_scope=False` after Exit C action.

Risk: delayed close after `block_new_entries` leaks into completed cycle.

Guardrail: same membership flag, explicit delayed-close tests.

Risk: reset-bar closes are lost.

Guardrail: accumulation-before-reset ordering and reset-bar tests for daily and
time-filter resets.

Risk: final-bar diagnostics imply an actual close that cannot materialize.

Guardrail: explicit final-bar contract and tests.

Risk: lite mode diverges from diagnostics mode.

Guardrail: runtime uses scalars only; diagnostic arrays are write-only mirrors.

Risk: existing Exit C priority regresses.

Guardrail: tie-break tests with `cycle_take_profit` first and existing exit
priority tests preserved.

Risk: strict schema/export tests fail after adding diagnostics.

Guardrail: update every strict keyset/display/header snapshot in the same gate.

Risk: `wf_grid` accidentally starts accepting wakeup runtime configs.

Guardrail: whitelist-only mirror plus explicit unsupported-pipeline regression.

## 21. Acceptance Checklist

- Config parses and validates `cycle_take_profit`.
- `wf_grid` still rejects wakeup configs as unsupported pipeline.
- New dataclass is exported from donor config and `wf_grid.config.schema`.
- `extract_trades` remains behavior-preserving after helper extraction.
- Runtime PnL accumulation uses the same helper's rounded `net_pnl_pct`.
- Micro-percent conversion uses shared truncation-safe helper.
- Trade helper preserves staged rounding and F-16 adjacency semantics exactly.
- Accumulator is integer micro-percent.
- Threshold uses `int(round(pnl_pct * 1e8))`.
- Open positions do not affect threshold.
- Reversal close is counted before new leg metadata is installed.
- `trade_entry_in_cycle_scope` prevents forced/delayed closes from leaking into
  completed cycles.
- Accumulation happens before daily/time reset.
- Reset-caused close is excluded from cycle PnL by explicit contract.
- Diagnostic snapshot uses tested accumulator, not post-reset value.
- Final-bar behavior is explicit and tested.
- `cycle_take_profit` is first in Exit C priority.
- `close_position` and `block_new_entries` match the specified semantics.
- Two Mode D diagnostics keys are added.
- Non-Mode-D diagnostics keyset remains unchanged.
- Lite mode returns `filter_diagnostics is None` and identical positions.
- Trade-level reason maps only for actual close-position attribution.
- Tester and WF/Grid summary builders expose or intentionally handle
  `wakeup_exit_cycle_take_profit_count`.
- Excel/export contracts include the new diagnostics columns and summary row.
- Focused and nearby regression commands pass.
