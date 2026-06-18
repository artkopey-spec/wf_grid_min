# Diagnostics v2 Performance Smoke

Date: 2026-06-18

Command shape:

```powershell
$env:PYTHONPATH='donor'
python <inline synthetic diagnostics v2 export smoke>
```

Fixture:

```text
rows: 300000
diagnostic_columns: 80
trades_per_period: 5000
periods: 5
signals: 5000
legacy full raw FilterDiagnostics_100: disabled
diagnostics_v2: enabled for all Phase A sheets
```

Measurement:

```text
baseline_seconds: 2.853
v2_seconds: 10.142
time_overhead_pct: 255.5
baseline_size_bytes: 1911513
v2_size_bytes: 3197460
size_overhead_pct: 67.3
FilterDiagnostics_sampled rows: 2000
```

Notes:

```text
Peak memory was not measured in this smoke.
The row cap contract for FilterDiagnostics_sampled is satisfied.
The current all-Phase-A default exceeds the original +30% time and +25% XLSX
size budget on this synthetic fixture. The overhead is dominated by additional
Phase A workbook content rather than full raw diagnostics export.
```
