# Mode D Wakeup ST Flip Real-Data Characterization

Date: 2026-06-08

Data/configs:

- `data.csv`
- `config_tester_wakeup_block_new_entries.yml`
- `config_tester_wakeup_close_position.yml`

Baseline:

- `before` was executed from an isolated `git archive HEAD` checkout under `tmp/mode_d_head_run/src`.
- The current uncommitted wakeup configs and `data.csv` were copied into that isolated checkout.
- `after` was executed from the current working tree.

Purpose:

- Confirm that Mode D no longer emits `opposite_st_flip` in `wakeup_exit_reason`.
- Confirm that internal ST flips are represented as `wakeup_position_action`.
- Measure real-data trade and diagnostic deltas caused by active wakeup cycles surviving internal ST flips.

## 100% Period Summary

| Config | Version | Trades | Sum PnL % | Max DD | Win Rate % | Trade exit reasons | Wakeup exit reasons | Wakeup actions | Opposite ST flip count |
| --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | ---: |
| block_new_entries | before | 1305 | -3.649050 | -0.047247 | 44.444444 | pending_open_trade_at_end=1; wakeup_exit_opposite_st_flip=1304 | none=55300; opposite_st_flip=1304; ttl=37 | n/a | 1304 |
| block_new_entries | after | 2296 | 3.617697 | -0.022817 | 37.935540 | filter_stopping_opposite_flip=453; pending_open_trade_at_end=1; wakeup_reverse_on_st_flip=1842 | none=56188; ttl=453 | exit_ttl=453; none=54123; reverse_on_st_flip=2065 | 0 |
| close_position | before | 1338 | -3.490871 | -0.046045 | 44.170404 | pending_open_trade_at_end=1; wakeup_exit_opposite_st_flip=1298; wakeup_exit_ttl=39 | none=55304; opposite_st_flip=1298; ttl=39 | n/a | 1298 |
| close_position | after | 2780 | 5.252493 | -0.025422 | 39.280576 | pending_open_trade_at_end=2; wakeup_exit_ttl=544; wakeup_reverse_on_st_flip=2234 | none=56097; ttl=544 | exit_ttl=544; none=53601; reverse_on_st_flip=2496 | 0 |

## All Period Deltas

### `config_tester_wakeup_block_new_entries.yml`

| Period | Version | Trades | Sum PnL % | Max DD | Win Rate % | Opposite ST flip count | Reverse actions | Exit TTL actions |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100% | before | 1305 | -3.649050 | -0.047247 | 44.444444 | 1304 | 0 | 0 |
| 100% | after | 2296 | 3.617697 | -0.022817 | 37.935540 | 0 | 2065 | 453 |
| 75% | before | 1044 | -3.101647 | -0.047247 | 44.923372 | 1043 | 0 | 0 |
| 75% | after | 1831 | 2.409258 | -0.022817 | 38.503550 | 0 | 1648 | 363 |
| 50% | before | 730 | -3.021080 | -0.040498 | 44.794521 | 729 | 0 | 0 |
| 50% | after | 1275 | 1.507256 | -0.022817 | 38.352941 | 0 | 1156 | 252 |
| 33% | before | 545 | -1.535899 | -0.040498 | 45.321101 | 544 | 0 | 0 |
| 33% | after | 942 | 1.052569 | -0.022120 | 38.322718 | 0 | 858 | 189 |
| 25% | before | 363 | -1.916026 | -0.040498 | 42.699725 | 362 | 0 | 0 |
| 25% | after | 629 | 2.227370 | -0.021270 | 37.360890 | 0 | 573 | 126 |

### `config_tester_wakeup_close_position.yml`

| Period | Version | Trades | Sum PnL % | Max DD | Win Rate % | Opposite ST flip count | Reverse actions | Exit TTL actions |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100% | before | 1338 | -3.490871 | -0.046045 | 44.170404 | 1298 | 0 | 0 |
| 100% | after | 2780 | 5.252493 | -0.025422 | 39.280576 | 0 | 2496 | 544 |
| 75% | before | 1075 | -2.978666 | -0.046045 | 44.651163 | 1040 | 0 | 0 |
| 75% | after | 2225 | 5.593155 | -0.025422 | 39.595506 | 0 | 1999 | 438 |
| 50% | before | 743 | -2.821879 | -0.038971 | 44.549125 | 728 | 0 | 0 |
| 50% | after | 1562 | 2.896660 | -0.025422 | 39.244558 | 0 | 1397 | 299 |
| 33% | before | 557 | -1.336692 | -0.038971 | 44.883303 | 543 | 0 | 0 |
| 33% | after | 1162 | 0.795049 | -0.025032 | 39.242685 | 0 | 1045 | 227 |
| 25% | before | 371 | -1.772098 | -0.038971 | 42.048518 | 361 | 0 | 0 |
| 25% | after | 769 | 1.799136 | -0.013446 | 39.531860 | 0 | 697 | 150 |

## Interpretation

- The new core-generated Mode D diagnostics have `wakeup_exit_opposite_st_flip_count == 0` for both configs and every period.
- Legacy `opposite_st_flip` wakeup exits are replaced by internal position actions, mostly `reverse_on_st_flip` for these `trade_mode: revers` configs.
- `block_new_entries` still closes held positions later via trade-level `filter_stopping_opposite_flip` after Exit C, preserving the lifecycle close attribution while keeping `wakeup_exit_reason` clean.
- The trade count increase is expected: internal ST flips no longer terminate the wakeup cycle, so the cycle can continue and produce additional position actions before TTL.
- The PnL and drawdown deltas are strategy-level behavior changes, not diagnostics-only changes. They match the implementation plan expectation that Mode D real-data results should change explainably.
