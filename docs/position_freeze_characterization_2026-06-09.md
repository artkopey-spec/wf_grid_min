# Position Freeze Characterization

Date: 2026-06-09

Scope:

- Data: `data.csv`
- Base config: `config_tester.yaml`
- Runtime: canonical `donor/supertrend_optimizer`
- Period: `100%`
- Grid:
  - baseline: `position_freeze.enabled=false`
  - enabled: `min_hold_bars=[2,3,4,5,6]`
  - trade modes: `long`, `short`, `revers`

Notes:

- Tester config surface uses `revers`; for this feature it is the same internal
  branch as `both`.
- `Whipsaw Derived = ignored - release_flat - release_reverse`. It includes
  early realignment/noop-style cleanup and is a derived bucket, not a trade PnL
  attribution bucket.
- `Confirmed Opposite = release_flat + release_reverse`.

## Results

| Mode | Hold | Trades | Sum PnL % | PF | Max DD | Avg Trade % | Bars<=3 Count | Bars<=3 Sum PnL % | Bars<=3 WR % | Ignored | Release Flat | Release Reverse | Release Noop | Whipsaw Derived | Confirmed Opposite |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| long | off | 192 | 1.355683 | 1.198702 | -0.011084 | 0.007061 | 76 | -3.413618 | 21.05 | 0 | 0 | 0 | 0 | 0 | 0 |
| long | 2 | 185 | 1.692462 | 1.255233 | -0.007538 | 0.009148 | 69 | -3.212434 | 23.19 | 32 | 25 | 0 | 6 | 7 | 25 |
| long | 3 | 179 | 1.876091 | 1.285763 | -0.008323 | 0.010481 | 23 | 0.370832 | 60.87 | 52 | 38 | 0 | 6 | 14 | 38 |
| long | 4 | 171 | 2.251850 | 1.354183 | -0.007700 | 0.013169 | 22 | 0.417482 | 63.64 | 65 | 43 | 0 | 8 | 22 | 43 |
| long | 5 | 165 | 2.373538 | 1.381643 | -0.008271 | 0.014385 | 22 | 0.417482 | 63.64 | 76 | 48 | 0 | 6 | 28 | 48 |
| long | 6 | 153 | 2.838720 | 1.473861 | -0.006910 | 0.018554 | 10 | 0.030444 | 60.00 | 80 | 40 | 0 | 12 | 40 | 40 |
| short | off | 233 | 0.391355 | 1.054553 | -0.011559 | 0.001680 | 107 | -4.427792 | 17.76 | 0 | 0 | 0 | 0 | 0 | 0 |
| short | 2 | 227 | 0.392026 | 1.053615 | -0.012382 | 0.001727 | 98 | -4.506082 | 19.39 | 56 | 50 | 0 | 5 | 6 | 50 |
| short | 3 | 221 | 0.272152 | 1.036288 | -0.013534 | 0.001231 | 24 | -0.215139 | 54.17 | 79 | 67 | 0 | 6 | 12 | 67 |
| short | 4 | 214 | 0.657133 | 1.092025 | -0.014315 | 0.003071 | 22 | -0.262748 | 54.55 | 91 | 72 | 0 | 7 | 19 | 72 |
| short | 5 | 206 | 0.322658 | 1.043902 | -0.015006 | 0.001566 | 21 | -0.222611 | 57.14 | 102 | 75 | 0 | 8 | 27 | 75 |
| short | 6 | 200 | 0.104862 | 1.013591 | -0.016451 | 0.000524 | 15 | -0.209878 | 60.00 | 112 | 79 | 0 | 6 | 33 | 79 |
| revers | off | 670 | 1.040585 | 1.049733 | -0.019115 | 0.001553 | 300 | -11.055970 | 22.33 | 0 | 0 | 0 | 0 | 0 | 0 |
| revers | 2 | 639 | 1.337864 | 1.066553 | -0.019688 | 0.002094 | 283 | -11.106106 | 24.03 | 133 | 0 | 117 | 12 | 16 | 117 |
| revers | 3 | 606 | -0.915094 | 0.956034 | -0.028085 | -0.001510 | 118 | -0.088280 | 50.00 | 188 | 0 | 152 | 15 | 36 | 152 |
| revers | 4 | 530 | 1.631117 | 1.090792 | -0.028839 | 0.003078 | 58 | -0.111406 | 48.28 | 214 | 0 | 116 | 15 | 98 | 116 |
| revers | 5 | 515 | 1.693659 | 1.097544 | -0.027667 | 0.003289 | 60 | -0.136953 | 48.33 | 218 | 0 | 120 | 16 | 98 | 120 |
| revers | 6 | 497 | 3.249029 | 1.202258 | -0.025247 | 0.006537 | 174 | 0.476714 | 47.70 | 213 | 0 | 115 | 19 | 98 | 115 |

## Review

- `long`: all enabled points improve Sum PnL, PF, Avg Trade, Max DD, and the
  early `Bars Held <= 3` loss bucket versus baseline. `H=6` is strongest on
  aggregate metrics, but `H=3..5` already remove most early losses.
- `short`: early `Bars Held <= 3` loss improves materially from `H=3`
  onward, but Max DD degrades as `H` grows. This is exactly the plan's primary
  long/short risk: delayed flat exits can hold confirmed adverse moves longer.
- `revers`: the early bucket improves sharply by `H=3`, but aggregate EV is
  not monotonic. `H=3` is negative and has materially worse drawdown; `H=6`
  is best on Sum PnL/PF among tested points while still showing higher DD than
  baseline.
- Release counters explain the deltas: `long`/`short` release mostly applies
  flat exits, while `revers` release applies reverse events. Derived whipsaw
  counts grow with larger windows, but confirmed-opposite costs are visible in
  the `short` DD and `revers H=3` degradation.

## Acceptance Impact

- Technical correctness acceptance is supported: diagnostics produce ignored,
  release-flat, release-reverse, and noop counters; baseline uses
  `enabled=false`.
- Strategy acceptance is conditional:
  - `long`: acceptable on this dataset.
  - `short`: acceptable only with explicit DD tolerance; prefer `H=3..4` if
    using this grid.
  - `revers`: do not choose `H=3`; `H=6` is the best tested point, but DD remains
    worse than baseline and needs owner sign-off.
