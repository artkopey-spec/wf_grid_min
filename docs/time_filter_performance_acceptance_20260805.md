# Time filter performance and IPC acceptance

Date: 2026-08-05  
Reference workstation: Windows, Python 3.14.6, pandas/NumPy from the project
environment.

The measurements cover `build_time_filter_runtime()` after the input
`DatetimeIndex` has already been loaded, as required by plan v3 section 21.
The canonical schedule is `09:00-12:00`, `16:00-18:00`.

## Reference measurements

| Bars | Best build | Maximum build | Peak temporary memory | Runtime arrays | Pickle payload | Payload limit |
|---:|---:|---:|---:|---:|---:|---:|
| 100,000 | 0.004226 s | 0.004469 s | 2.103 MB | 2.000 bytes/bar | 200,369 B | 365,536 B |
| 500,000 | 0.021220 s | 0.021713 s | 10.494 MB | 2.000 bytes/bar | 1,000,369 B | 1,565,536 B |

All absolute time, memory, persistent-array, and serialized-payload budgets
pass.

The production dataset contains 105,681 bars. Its measured Time runtime build
was 0.004771 seconds. The full sequential WF smoke took 414.8 seconds, so the
one-time mask build represents 0.001150% of that wall-clock time, below the 1%
acceptance limit.

## Automated regression gate

`wf_grid/tests/test_time_filter_performance.py` checks both 100k and 500k
reference sizes for:

- build time no greater than 1.0 second;
- peak temporary allocation no greater than 32 MB beyond the prebuilt index;
- persistent arrays no greater than 3 bytes/bar;
- pickle payload no greater than 3 bytes/bar plus 64 KB;
- slice schedule reuse and a cleared first reset.

Worker initializer reuse and sequential/parallel equality remain covered by
`wf_grid/tests/test_time_filter_wf_runtime.py`.
