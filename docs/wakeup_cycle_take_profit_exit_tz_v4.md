# TZ v4: Wakeup Cycle Take Profit Exit

## 1. Назначение

Добавить Exit C `cycle_take_profit` для `trade_filter.wakeup_regime` в ZigZag
Mode D.

Exit завершает текущий wakeup-cycle, когда сумма realized net PnL закрытых
сделок, принадлежащих активному wakeup-cycle, достигает порога. Открытая
позиция не участвует в расчете.

Фича включается только в tester pipeline. WF/Grid продолжает отклонять
`wakeup_regime` через gate `wakeup_regime_unsupported_pipeline`.

## 2. Конфиг

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

Validation:

- `enabled` must be strict bool;
- `pnl_pct` is required when `enabled=true`;
- `pnl_pct` must be numeric, finite, and `> 0`;
- reject bool, `0`, negative numbers, strings like `"1%"`, and `null`;
- `cycle_take_profit.enabled=true` counts as enabled Exit C condition;
- if `wakeup_regime.enabled=false`, a valid nested block is accepted and is
  runtime no-op.

WF/Grid:

- whitelist recognizes the new config path;
- config still fails with `wakeup_regime_unsupported_pipeline`, not unknown key.

## 3. PnL Source Of Truth

The single production source of closed-leg economics is:

```python
closed_leg_trade_economics(
    entry_idx,
    exit_idx,
    direction,
    positions,
    execution_prices,
    commission_rate,
) -> {
    "gross_pnl_pct": float,
    "commission_pct": float,
    "net_pnl_pct": float,
}
```

Both `extract_trades` and the Mode D runtime accumulator must use this helper.
There must not be a second production implementation of trade economics.
The helper may return the full economics triplet for `extract_trades`; the FSM
currently uses only `net_pnl_pct`. Before removing the WF/Grid wakeup gate,
revisit this API and consider a lean tuple/namedtuple or net-only fast path.

The helper must preserve current `extract_trades` behavior exactly:

- simple entry/exit gross PnL;
- open-to-open prices;
- base commission from `commission_per_bar[entry_idx:exit_idx]`;
- opening commission from flat when `positions[entry_idx - 1] == 0`;
- F-16 reversal split with the same adjacency semantics as current production
  post-processing;
- staged rounding exactly as current production:
  1. base values rounded to 6 decimals;
  2. opening-from-flat adjustment applied to already-rounded values and rounded
     again;
  3. reversal split applied to already-rounded values and rounded again.

Golden fixtures must be captured from current production output before the
helper refactor. Hand-authored expected economics numbers are not acceptable.

## 4. Integer Precision Contract

Runtime accumulator:

```text
cycle_realized_pnl_micropct: int
```

Shared conversion helper:

```python
def net_pnl_pct_to_micropct(net_pnl_pct: float) -> int:
    return int(round(round(float(net_pnl_pct), 6) * 1_000_000))
```

The outer `round()` before `int()` is mandatory. It prevents binary-float
truncation on boundary values.

Threshold:

```python
threshold_micropct = int(round(float(pnl_pct) * 1e8))
trigger = cycle_realized_pnl_micropct >= threshold_micropct
```

Diagnostics mirror:

```python
wakeup_cycle_realized_pnl_pct[t] = tested_cycle_pnl_micropct / 1e8
```

`net_pnl_pct` is in percent units. Dividing micro-percent by `1e8` gives
fraction units.

## 5. Runtime State

Add Mode D loop-local state:

```python
cycle_realized_pnl_micropct = 0
tested_cycle_pnl_micropct = 0
trade_entry_idx = -1
trade_entry_pos = 0
trade_entry_in_cycle_scope = False
```

`trade_entry_in_cycle_scope` is required. It defines whether the open leg
belongs to the active wakeup-cycle. Current FSM state alone is insufficient
because forced close and `ST_STOPPING` delayed close can materialize after the
cycle is already inactive.

Scope state table:

```text
event                                                scope after event
---------------------------------------------------  -----------------
wakeup opens real position in ST_ACTIVE_FREEZE       True
active-cycle reversal opens new leg                  True, unless Exit C fires same bar
restore/release opens from flat in active cycle       True
materialized flat close while still active            False after accumulation
cycle_take_profit close_position fires                False immediately
cycle_take_profit block_new_entries fires             False immediately
daily/time reset fires                                False
opposite ST flip ends cycle                           False
ST_STOPPING delayed close                             False
new wakeup-cycle starts                               reset accumulator; real entry sets True
```

Same-bar reversal plus `cycle_take_profit`:

- old leg is accumulated first;
- new reversal leg may receive metadata with `entry_idx=t`;
- if `cycle_take_profit` fires on the same bar, set
  `trade_entry_in_cycle_scope=False` for the new leg;
- the new leg's forced/delayed close cannot contribute to the threshold that
  fired.

## 6. Bar Order

Mode D bar processing order:

```text
0. Capture immutable bar-start snapshots.
1. Accumulate materialized close from positions[t - 1] -> positions[t].
2. Store tested_cycle_pnl_micropct.
3. Apply combined daily/time reset.
4. Start new wakeup-cycle from OFF if entry conditions pass.
5. Evaluate Exit C chain.
6. Write diagnostics snapshot for the tested value.
7. Apply ST flip / position-freeze / release logic.
8. Decide filtered_positions[t + 1].
9. Persist remaining diagnostics.
```

Materialized close detection:

```text
t > 0
AND trade_entry_idx >= 0
AND trade_entry_pos != 0
AND filtered_positions[t - 1] != 0
AND filtered_positions[t] != filtered_positions[t - 1]
```

Accumulate only when `trade_entry_in_cycle_scope is True`.

Reset-close semantic:

- if reset on bar `t` requests close by writing `filtered_positions[t + 1]=0`,
  that close is unrealized at reset time;
- it materializes only on the next transition;
- because the cycle has already reset, this reset-caused close is excluded from
  cycle PnL by design.

## 7. Exit C Predicate

Priority chain:

```text
1. cycle_take_profit
2. ttl
3. no_fresh_candidate
4. local_median_stop
5. cycle_trade_limit
```

Predicate gates:

```text
state_at_bar_start == ST_ACTIVE_FREEZE
state == ST_ACTIVE_FREEZE
not wakeup_exit_c_fired
not is_reset
cycle_take_profit_enabled
tested_cycle_pnl_micropct >= threshold_micropct
```

`close_position` action:

- `wakeup_exit_reason = "cycle_take_profit"`;
- `wakeup_position_action = "exit_cycle_take_profit"`;
- `wakeup_exit_cycle_take_profit_triggered[t] = 1`;
- `wakeup_exit_close_triggered[t] = 1`;
- cycle transitions to OFF;
- accumulator resets after diagnostics snapshot captures tested value;
- `trade_entry_in_cycle_scope = False`;
- actual close materializes through existing `filtered_positions[t] ->
  filtered_positions[t + 1]` flow when `t + 1 < n`.

`block_new_entries` action:

- same reason/action/trigger flag;
- cycle transitions to `ST_STOPPING`;
- current position is not closed immediately;
- `trade_entry_in_cycle_scope = False`;
- delayed close attribution remains `filter_stopping_opposite_flip`.

One-shot gate: existing `wakeup_exit_c_fired`.

## 8. Final-Bar Contract

`apply()` cannot materialize a new close when `t + 1 >= n`.

If `cycle_take_profit` fires on the final bar:

- diagnostics/action flags may be set;
- no new close is materialized inside `apply()`;
- open position remains pending/open-at-end for `extract_trades`;
- `wakeup_exit_close_triggered=1` still means action requested.

Do not add special finalization close in this feature.

## 9. Diagnostics

Add Mode D diagnostics only:

```text
wakeup_cycle_realized_pnl_pct            float64 fraction
wakeup_exit_cycle_take_profit_triggered  int8
```

Lite mode:

- no diagnostic arrays allocated;
- `filter_diagnostics is None`;
- positions identical to diagnostic mode.

Mode D keyset gains exactly these two runtime diagnostics keys. Non-Mode-D
keyset remains unchanged.

## 10. Trade-Level Mapping

Extend mappings:

```text
_wakeup_exit_reason_at:
  "cycle_take_profit" -> "wakeup_exit_cycle_take_profit"

_wakeup_position_action_at:
  "exit_cycle_take_profit" -> "wakeup_exit_cycle_take_profit"
```

Expected attribution:

- `close_position` can produce
  `exit_reason == "wakeup_exit_cycle_take_profit"`;
- `block_new_entries` delayed close remains
  `filter_stopping_opposite_flip`;
- final-bar requested close does not fabricate actual trade-level close.

## 11. Summary And Export

Tester summary:

```text
wakeup_exit_cycle_take_profit_count =
  sum(wakeup_exit_cycle_take_profit_triggered)
```

WF/Grid summary:

- count `wakeup_exit_cycle_take_profit_triggered` only if the diagnostics key
  exists;
- do not infer wakeup semantics from config;
- while gate remains active, real WF/Grid runs do not produce the key.

WF/Grid collector:

- include `wakeup_exit_cycle_take_profit_count` in `_FILTER_SUMMARY_COLUMNS`;
- gated real runs flatten this value as `None`;
- synthetic diagnostics containing the key must be preserved through summary
  and collector flattening.

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

## 12. Acceptance Tests

Config:

```text
valid enabled=true pnl_pct values parse
enabled=false without pnl_pct accepted
enabled=true without pnl_pct rejected
invalid pnl_pct values rejected
cycle_take_profit can be the only enabled Exit C condition
wf_grid rejects through wakeup_regime_unsupported_pipeline
```

Economics:

```text
extract_trades unchanged after helper extraction
flat-to-flat identical
reversal F-16 split identical
pending/open-at-end identical
daily/time reset close identical
commission=0 identical
optimizer and wf_grid backtest paths produce identical trades_df after helper extraction
```

Runtime:

```text
closed +0.0099 at threshold 0.01 -> no trigger
closed +0.0100 at threshold 0.01 -> trigger
unrealized open PnL does not trigger
new wakeup-cycle starts with accumulator 0
close_position closes when t+1 exists
final-bar close_position is action-requested only
block_new_entries does not close immediately
cycle_take_profit beats ttl
one-shot per cycle
reset-bar accumulates already-materialized close before reset
reset-caused close is excluded
commission_rate=0 works
lite and diagnostics positions identical
```

Membership:

```text
cycle_realized_pnl_micropct
==
sum(net_pnl_pct_to_micropct(trades_df.net_pnl_pct) for member trades)
```

No float tolerance.

Diagnostics/export:

```text
Mode D adds exactly two runtime diagnostics keys
non-Mode-D keyset unchanged
trade-level close_position maps to wakeup_exit_cycle_take_profit
block_new_entries delayed close is not over-attributed
tester summary exposes wakeup_exit_cycle_take_profit_count
WF/Grid summary counts synthetic key and real gated runs stay rejected
Excel diagnostics and summary rows are updated
strict keyset/dtype/display/header snapshots updated
early_exit/truncated artifacts remain index-compatible
```

## 13. Out Of Scope

```text
Removing or weakening wakeup_regime_unsupported_pipeline for WF/Grid.
Changing open-to-open execution model.
Changing wakeup_exit_close_triggered from action-requested to actual-close semantics.
Changing delayed-close trade attribution for block_new_entries.
Adding final-bar forced close materialization.
```
