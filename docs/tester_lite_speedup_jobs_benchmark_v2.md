# Tester Lite Speedup Jobs Benchmark v2

Date: 2026-06-16

## Config Set

Source:

```text
C:\3.1_wf_grid\config tester
```

Glob:

```text
modeD_atr_freeze_0[0-2][0-9]_long*.yaml
```

Resolved configs:

```text
29
```

All selected configs are lite configs:

```text
export.diagnostics=false
export.signals=false
export.false_start=false
export.cycle=false
export.trades=false
```

Data:

```text
C:\3.1_wf_grid\data.csv
```

## Command Template

```powershell
python run_configs_tester_parallel.py `
  --jobs <N> `
  --configs-dir "config tester" `
  --output-dir _bench/jobs_<N>_run_<R> `
  --csv data.csv `
  --glob "modeD_atr_freeze_0[0-2][0-9]_long*.yaml" `
  --summary-format json
```

## Results

`span_sec` and `configs_min` are computed from summary row `started_at` /
`finished_at` timestamps, so resolution is one second.

| jobs | run 1 configs/min | run 2 configs/min | run 3 configs/min | median configs/min | failures |
|---:|---:|---:|---:|---:|---:|
| 4 | 34.800 | 36.250 | 35.510 | 35.510 | 0 |
| 6 | 42.439 | 42.439 | 40.465 | 42.439 | 0 |
| 8 | 41.429 | 42.439 | 42.439 | 42.439 | 0 |
| 10 | 43.500 | 42.439 | 43.500 | 43.500 | 0 |

Raw median spans:

| jobs | median span sec | configs |
|---:|---:|---:|
| 4 | 49 | 29 |
| 6 | 41 | 29 |
| 8 | 41 | 29 |
| 10 | 40 | 29 |

## Machine

```text
CPU: 12th Gen Intel Core i3-1215U
Architecture: hybrid — 2 P-cores (HT, 4 logical) + 4 E-cores (no HT, 4 logical) = 8 logical CPUs
RAM: 8 GB
```

## Recommendation

Recommended default for this benchmark machine/path:

```text
--jobs 6
```

Reason:

```text
The i3-1215U has 2 Performance cores (HT) + 4 Efficiency cores. E-cores are
significantly weaker than P-cores. The benchmark shows a real throughput jump
from jobs=4 to jobs=6 (+19%), but a plateau at jobs=6/8/10 (42.44/42.44/43.50
configs/min) — all within 1-second timestamp resolution noise. The apparent
jobs=10 lead of one second of span is not a reliable signal on this CPU.

jobs=6 uses P-cores at full capacity plus two E-cores and avoids oversubscription.
```

Operational note:

```text
jobs=8 is acceptable for unattended batch runs where the machine is idle.
jobs=10 is not recommended: it causes oversubscription on an 8-logical-CPU
machine and the apparent throughput gain is within measurement noise.
```
