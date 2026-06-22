# Implementation Plan v1: Wakeup Cycle Take Profit Exit

## 1. Цель

Добавить tester-only Exit C `cycle_take_profit` для Mode D
`trade_filter.wakeup_regime`.

Exit завершает текущий wakeup-cycle, когда сумма realized net PnL закрытых
сделок внутри активного цикла достигает порога:

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

`pnl_pct` задается как fraction: `0.01` означает `+1%`. В runtime сумма
ведется в integer micro-percent, а не во float.

## 2. Границы

Фича включается только в tester pipeline. `wf_grid` продолжает reject'ить
`wakeup_regime` через существующий gate `wakeup_regime_unsupported_pipeline`.

В `wf_grid` меняются только schema/summary parity участки:

- whitelist распознает новый config path, чтобы ошибка оставалась
  `unsupported_pipeline`, а не `unknown key`;
- summary builder умеет посчитать новый counter, если такой diagnostics key
  когда-нибудь пришел в `filter_diagnostics`.

Не меняем:

- поддержку Mode D в `wf_grid`;
- open-to-open contract;
- семантику `wakeup_exit_close_triggered` как action-requested;
- trade-level attribution для `block_new_entries`;
- поведение configs без `cycle_take_profit` или с `enabled: false`.

## 3. Основные файлы

Config:

```text
donor/supertrend_optimizer/core/trade_filter_config.py
wf_grid/config/loader.py
wf_grid/tests/test_wp2_config_trade_filter.py
donor/tests/core/test_wakeup_config_dataclasses.py
donor TESTER/tests/test_wp_t2_load_tester_config.py
```

PnL source of truth:

```text
donor/supertrend_optimizer/core/trades.py
donor/tests/core/test_extract_trades_last_bar.py
donor/tests/core/test_invariants_pnl.py
```

Runtime:

```text
donor/supertrend_optimizer/core/zigzag_st_filter.py
donor/supertrend_optimizer/core/backtest.py
wf_grid/tests/test_wakeup_mode_d_entry.py
```

Diagnostics, enrichment, export, summary:

```text
donor/supertrend_optimizer/core/filter_trade_diagnostics.py
donor/supertrend_optimizer/testing/runner.py
donor/supertrend_optimizer/io/excel_tester.py
wf_grid/wf/step_executor.py
wf_grid/tests/test_pr5_schema_contract.py
wf_grid/tests/test_pr6_excel_contract.py
wf_grid/tests/test_xlsx_export.py
donor TESTER/tests/test_phase2_wp_t7_excel_export.py
donor TESTER/tests/test_xlsx_cycle_sheet.py
```

## 4. WP1 - Config Contract

Add dataclass:

```python
@dataclass
class TradeFilterWakeupCycleTakeProfitExitConfig:
    enabled: object = False
    pnl_pct: object = None
```

Extend `TradeFilterWakeupExitConfig`:

```python
cycle_take_profit: TradeFilterWakeupCycleTakeProfitExitConfig = field(
    default_factory=TradeFilterWakeupCycleTakeProfitExitConfig
)
```

Parser and whitelists:

- read `exit_raw.get("cycle_take_profit") or {}`;
- add `cycle_take_profit` under `trade_filter.wakeup_regime.exit`;
- add child whitelist:

```text
trade_filter.wakeup_regime.exit.cycle_take_profit:
  enabled, pnl_pct
```

Validation in `_validate_wakeup_regime_block()`:

- `enabled` must be strict bool when present;
- when `enabled is True`, `pnl_pct` is required;
- `pnl_pct` must be numeric, finite, and `> 0`;
- reject `0`, negative numbers, `"1%"`, `None` when enabled;
- `cycle_take_profit.enabled=True` counts as an enabled Exit C condition;
- if `wakeup_regime.enabled=False`, a valid nested block is accepted and is
  runtime no-op;
- `wf_grid` config with the block still fails through
  `wakeup_regime_unsupported_pipeline`.

Tests:

```text
valid enabled=true pnl_pct in {0.01, 0.005, 0.025}
enabled=false without pnl_pct is accepted
enabled=true without pnl_pct is rejected
pnl_pct invalid values are rejected
enabled invalid values are rejected
unknown nested keys are rejected
cycle_take_profit can be the only enabled Exit C condition
wf_grid rejects as unsupported pipeline, not unknown key
shared/wf_grid whitelist parity remains green
```

## 5. WP2 - Trade Economics Helper

Extract a reusable helper from `extract_trades`:

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

Return exactly rounded values:

```text
gross_pnl_pct
commission_pct
net_pnl_pct
```

Rules to preserve from current `trades.py`:

- gross PnL from simple entry/exit price return;
- base commission over `[entry_idx:exit_idx)`;
- opening commission from flat when `positions[entry_idx - 1] == 0`;
- reversal entry side gets `commission_rate * 100`;
- reversal closing side gives back `commission_rate * 100` from the base
  closing trade commission;
- round all three values to 6 decimals.

Refactor `extract_trades` to call this helper for every trade row, including:

- flat-to-flat;
- reversal close/open;
- pending/open-at-end;
- reset/time close.

Do not keep a second production implementation beside it. If a legacy oracle is
needed, keep it only in tests.

Regression tests:

```text
golden trades_df rows are identical before/after helper extraction
flat-to-flat economics identical
reversal F-16 split identical
pending/open-at-end economics identical
daily/time reset close economics identical
commission conservation invariant still passes
```

## 6. WP3 - `apply()` Plumbing

Extend signature:

```python
apply(..., commission_rate: float | None = None)
```

Runtime rules:

- `commission_rate` is required only when
  `cycle_take_profit.enabled is True`;
- if enabled and `commission_rate is None`, raise clear `RuntimeError` or
  `ValueError`;
- if disabled, existing direct unit tests may keep `commission_rate=None`;
- `open_prices` changes from API-symmetry parameter to required execution
  prices when the feature is enabled;
- validate `open_prices` length and positivity in the enabled branch.

Call sites:

- pass `commission_rate` from `donor/supertrend_optimizer/core/backtest.py`
  into `_zz_apply`;
- check tester runner direct calls that may call `apply()` without backtest;
- do not add `caller_pipeline` to `apply()`.

Tests:

```text
apply() without feature accepts commission_rate=None
enabled=true + commission_rate=None raises clear error
enabled=true + open_prices=None raises clear error
backtest.py passes commission_rate into _zz_apply
```

## 7. WP4 - Runtime State And Membership

Add loop-local scalars in `apply()`:

```python
cycle_realized_pnl_micropct = 0
trade_entry_idx = -1
trade_entry_pos = 0
```

Update entry metadata:

- wakeup start: `trade_entry_idx = t`, `trade_entry_pos = held_pos`;
- internal reversal: first accumulate old leg, then set new entry metadata to
  `t` and new position;
- restore from flat on ST flip/freeze release: set entry metadata to `t`;
- flat: reset metadata to `-1, 0`;
- forced close: keep metadata until the materialized
  `filtered_positions[t] -> filtered_positions[t + 1]` transition is observed
  on the next bar.

Reset `cycle_realized_pnl_micropct` together with per-cycle state:

- combined daily/time reset;
- new wakeup trigger;
- Exit C `close_position`;
- Exit C `block_new_entries` transition out of active cycle;
- `ST_STOPPING -> OFF`;
- opposite ST flip ending the cycle;
- any actual OFF transition in Mode D.

Guardrail: PnL from one wakeup-cycle must never carry into the next one.

## 8. WP5 - Accumulate At Bar Start

At the beginning of processing bar `t`, after immutable bar-start snapshots and
before the Exit C predicate chain, detect a materialized close:

```text
t > 0
AND trade_entry_idx >= 0
AND trade_entry_pos != 0
AND filtered_positions[t - 1] != 0
AND filtered_positions[t] != filtered_positions[t - 1]
```

If the wakeup-cycle was active for that closed leg:

```python
leg = closed_leg_trade_economics(
    trade_entry_idx,
    t,
    trade_entry_pos,
    positions_view_with_extract_trades_semantics,
    open_prices,
    commission_rate,
)
leg_micropct = int(round(leg["net_pnl_pct"], 6) * 1_000_000)
cycle_realized_pnl_micropct += leg_micropct
```

`positions_view_with_extract_trades_semantics` means the same materialized
position-array semantics that `extract_trades()` will later consume. Do not
compute cycle PnL from a separate shadow position model.

Important ordering:

1. Accumulate closed leg PnL from already materialized transition.
2. Apply combined reset if present.
3. Start new wakeup cycle from OFF, accumulator becomes `0`.
4. Run Exit C chain.
5. Write diagnostics snapshot for the value tested on this bar.
6. Decide `filtered_positions[t + 1]`.

Reversal semantics:

- old leg closes at `exit_idx=t` and contributes to the accumulator;
- new leg metadata is set to `entry_idx=t`;
- if `cycle_take_profit` fires on the same bar, the new leg forced close happens
  as `filtered_positions[t] -> filtered_positions[t + 1]`;
- that forced close must not contribute to the threshold that fired on bar `t`.

## 9. WP6 - Exit C Predicate

Extend `_WakeupExitConfigParts` and `_resolve_mode_d_wakeup_exit()` with
`cycle_take_profit`.

Add mapping:

```python
if reason == "cycle_take_profit":
    return "exit_cycle_take_profit"
```

Priority chain becomes:

```text
cycle_take_profit
ttl
no_fresh_candidate
local_median_stop
cycle_trade_limit
```

Predicate:

```python
threshold_micropct = int(round(cycle_take_profit.pnl_pct * 1e8))
cycle_realized_pnl_micropct >= threshold_micropct
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

- `close_position`: set reason/action, set close-trigger flag, transition to
  OFF, write `filtered_positions[t + 1] = 0` through existing close flow;
- `block_new_entries`: transition to `ST_STOPPING`, block new entries, do not
  close current position immediately;
- one-shot via existing `wakeup_exit_c_fired`.

Tie-break tests must prove:

```text
cycle_take_profit + ttl on same bar -> reason == "cycle_take_profit"
cycle_take_profit fires no more than once per wakeup-cycle
```

## 10. WP7 - Diagnostics

Add Mode D diagnostics fields:

```text
wakeup_cycle_realized_pnl_pct            float64 fraction
wakeup_exit_cycle_take_profit_triggered  int8
```

Allocation only when `collect_filter_diagnostics=True`, following existing
array allocation style.

Snapshot value:

```python
wakeup_cycle_realized_pnl_pct[t] = cycle_realized_pnl_micropct / 1e8
```

The snapshot must show the value tested by Exit C on the trigger bar: after
accumulation/new-cycle setup, before the reset caused by the Exit C action.

Lite mode:

- no diagnostic arrays allocated;
- `filter_diagnostics is None`;
- positions match diagnostic mode bit-for-bit.

Mode D keyset acceptance:

- exactly two new diagnostics keys for this feature;
- non-Mode-D diagnostics keyset unchanged.

## 11. WP8 - Trade-Level Mapping

Extend `filter_trade_diagnostics.py`:

```text
_wakeup_exit_reason_at:
  cycle_take_profit -> wakeup_exit_cycle_take_profit

_wakeup_position_action_at:
  exit_cycle_take_profit -> wakeup_exit_cycle_take_profit
```

Expected trade-level behavior:

- `close_position` can produce
  `exit_reason == "wakeup_exit_cycle_take_profit"`;
- `block_new_entries` keeps existing delayed close attribution, usually
  `filter_stopping_opposite_flip`;
- do not change enrichment timing in this task.

Tests:

```text
close_position maps to wakeup_exit_cycle_take_profit
block_new_entries does not over-attribute delayed close
old ttl/no_fresh/local_median/cycle_trade_limit mappings remain unchanged
reset/opposite-ST-flip mappings remain unchanged
```

## 12. WP9 - Summary And Excel

Tester summary:

```python
"wakeup_exit_cycle_take_profit_count": _sum_int8(
    "wakeup_exit_cycle_take_profit_triggered"
)
```

WF/Grid parity summary:

- `_compute_filter_diagnostics_summary()` counts
  `wakeup_exit_cycle_take_profit_triggered` if present;
- if the key is absent, behavior follows existing local style: key omitted or
  value `0`;
- no config-derived wakeup semantics in WF/Grid.

Excel display names:

```text
wakeup_cycle_realized_pnl_pct
  -> Wakeup Cycle Realized PnL Pct
wakeup_exit_cycle_take_profit_triggered
  -> Wakeup Exit Cycle Take Profit Triggered
```

Summary row:

```text
Wakeup Exit Cycle Take Profit
```

Place it near other wakeup exits:

```text
Wakeup Exit TTL
Wakeup Exit No Fresh Candidate
Wakeup Exit Local Median Stop
Wakeup Exit Cycle Take Profit
Wakeup Exit Close
```

Update strict display/header snapshots wherever they mirror
`FILTER_DIAGNOSTICS_100_DISPLAY_NAMES`.

## 13. WP10 - Runtime Acceptance Tests

Use deterministic direct `apply(..., per_bar=ZigZagPerBar(...))` tests where
possible. Do not rely on emergent synthetic OHLC when the target is exact PnL
threshold behavior.

Core cases:

```text
enabled=false preserves old positions
pnl_pct=0.01, closed +0.0099 -> no trigger
pnl_pct=0.01, closed +0.0100 -> trigger
pnl_pct=0.01, closed +0.0120 -> one trigger and cycle -> OFF
unrealized open position does not affect threshold
new wakeup-cycle starts with accumulator 0
close_position sets wakeup_exit_close_triggered=1 and positions go flat
block_new_entries does not close current position immediately
cycle_take_profit beats ttl on same bar
trigger flag is one-shot per wakeup-cycle
diagnostic snapshot on trigger bar shows value reaching threshold
collect_filter_diagnostics=False returns None diagnostics
lite and diagnostic positions are identical
```

Exact membership tests:

```text
cycle_realized_pnl_micropct
==
sum(int(round(trades_df.net_pnl_pct, 6) * 1_000_000) for member trades)
```

Cover:

- flat-to-flat;
- reversal with F-16 split;
- forced close from `close_position` excluded from the threshold that caused it;
- `ST_STOPPING` delayed close excluded after cycle is inactive.

No float tolerance in the membership assertion.

## 14. Implementation Order

1. Config dataclass, parser, shared whitelist, validator, `__all__`.
2. `wf_grid/config/loader.py` whitelist parity and unsupported-pipeline test.
3. Golden/reference tests for current `trades_df` behavior.
4. Extract `closed_leg_trade_economics()` and refactor `extract_trades`.
5. `apply()` signature plumbing, enabled-branch validation, `backtest.py`
   `commission_rate` pass-through.
6. Add runtime predicates:
   `wakeup_cycle_take_profit_enabled` and `threshold_micropct`.
7. Add trade metadata scalars and OFF/reset handling.
8. Add bar-start closed-leg accumulation.
9. Add Exit C priority branch before TTL.
10. Add diagnostic arrays and Mode-D finalization keys.
11. Add trade-level reason/action mapping.
12. Add tester and WF/Grid summary counters.
13. Add Excel display names, summary row, strict snapshot updates.
14. Runtime acceptance tests and lite parity tests.
15. Focused verification, then nearby regression suite.

## 15. Verification Commands

Focused config and trades:

```powershell
python -m pytest wf_grid/tests/test_wp2_config_trade_filter.py -q
python -m pytest donor/tests/core/test_wakeup_config_dataclasses.py -q
python -m pytest donor/tests/core/test_extract_trades_last_bar.py donor/tests/core/test_invariants_pnl.py -q
```

Runtime:

```powershell
python -m pytest wf_grid/tests/test_wakeup_mode_d_entry.py -q
python -m pytest wf_grid/tests/test_wp6_st_flip_event_ordering.py wf_grid/tests/test_wp7_backtest_integration.py -q
```

Export and summary:

```powershell
python -m pytest wf_grid/tests/test_pr5_schema_contract.py wf_grid/tests/test_pr6_excel_contract.py wf_grid/tests/test_xlsx_export.py -q
python -m pytest "donor TESTER/tests/test_phase2_wp_t7_excel_export.py" "donor TESTER/tests/test_xlsx_cycle_sheet.py" -q
```

Final non-slow sweep if time allows:

```powershell
python -m pytest wf_grid/tests -m "not slow" -q
```

## 16. Main Risks And Guardrails

Risk: helper does not match old `trades_df` after commission post-processing.
Guardrail: golden fixtures before runtime work, including reversal and pending
trades.

Risk: forced close PnL is counted into the threshold that requested it.
Guardrail: accumulate only materialized closes at the start of the next bar
while the cycle was active.

Risk: accumulator leaks across wakeup cycles.
Guardrail: reset at every actual cycle end and new cycle start; strict
membership test without float tolerance.

Risk: lite mode diverges from diagnostics mode.
Guardrail: runtime uses scalars only; diagnostic arrays are write-only mirrors.

Risk: Exit C priority regression for existing exits.
Guardrail: preserve old TTL/no-fresh/local-median/cycle-limit tests and add
tie-break tests with `cycle_take_profit` first.

Risk: `wf_grid` accidentally starts accepting wakeup runtime configs.
Guardrail: whitelist-only mirror plus explicit unsupported-pipeline regression.

## 17. Acceptance Checklist

- Config parses and validates `cycle_take_profit`.
- `wf_grid` still rejects wakeup configs as unsupported pipeline.
- `extract_trades` remains behavior-preserving after helper extraction.
- Runtime PnL accumulation uses the helper's rounded `net_pnl_pct`.
- Accumulator is integer micro-percent.
- Threshold uses `int(round(pnl_pct * 1e8))`.
- Open positions do not affect threshold.
- Reversal close is counted before same-bar new leg metadata is installed.
- `cycle_take_profit` is first in Exit C priority.
- `close_position` and `block_new_entries` match the ТЗ semantics.
- Two diagnostics keys are added and are Mode-D-only.
- Lite mode returns `filter_diagnostics is None` and identical positions.
- Trade-level reason maps only for `close_position` acceptance.
- Tester and WF/Grid summary builders expose
  `wakeup_exit_cycle_take_profit_count`.
- Excel/export contracts include the new diagnostics columns and summary row.
- Focused and nearby regression commands pass.
