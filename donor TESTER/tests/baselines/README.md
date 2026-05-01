# Tester Baselines — WP-T1 (rebaseline against Mode C runtime)

Disabled-run snapshots used by golden tests #17 and #19 (Phase 2 plan §14 WP-T1
acceptance). Only valid against the canonical Mode C runtime —
`donor/supertrend_optimizer/`.

> **DO NOT overwrite these files without explicit owner sign-off and `--update-golden` flag.**
> Silent regeneration is forbidden per plan §14 WP-T8 (audit-fix v0.5).

## Engine source (Mode C runtime)

| Property | Value |
|---|---|
| Active package | `donor/supertrend_optimizer/` (regular package, `__file__` set) |
| Runtime contract | Top-level resolution from `donor/`; `donor TESTER/` follows in `sys.path` only as fallback for tester-only artifacts |
| Order enforcement | `donor TESTER/tests/conftest.py` — `donor/` inserted at `sys.path[0]` |
| Smoke gate | `donor TESTER/tests/test_wp_t2_packaging_smoke.py` |

## Rebaseline history

| Capture | Engine | Status | Notes |
|---|---|---|---|
| 2026-04-28 (initial WP-T1) | `donor TESTER/supertrend_optimizer/` (stub) | **invalidated** | Captured before B-2 unblocker; `donor TESTER/` engine differs from active donor (different `engine/run.py`, no `core/zigzag_st_filter.py`) |
| 2026-04-28 (WP-T2 unblocker rebaseline) | `donor/supertrend_optimizer/` (Mode C) | **canonical** | This is the golden reference for #17 / #19 |

See `docs/wp_t3_tester_dedup_log.md` for the audit trail.

## Reference dataset

| Property | Value |
|---|---|
| Path (relative to repo root) | `donor TESTER/data.csv` |
| SHA-256 | `6C3C9CA8BD134106DEFF371C00B26E727FE7275213BDAB662F0F10EBDEC18A56` |
| File size | 5 429 164 bytes |
| Row count (data rows, excl. header) | 109 421 |
| Datetime span (first bar) | 2025-11-24 07:00:00+03:00 |
| Datetime span (last bar) | 2026-04-16 11:19:00+03:00 |
| OHLC columns | time, open, high, low, close |

## Config used

File: `donor TESTER/config_tester.yaml` (as-is, no `trade_filter` block — i.e.
filter disabled per Appendix A v1.1 §11.1).

Key settings at capture time:
- `supertrend.atr_period: 18`
- `supertrend.multiplier: 1.5`
- `trade_mode: long`
- `commission: 0.0003`
- `warmup_period_auto: true` → effective warmup = 400 bars
- `segmentation.mode: equal_blocks / legacy` (two separate runs)
- `segmentation.n_parts: 7`
- `periods_per_year: auto` → resolved 247716.00 (1m intraday data on stocks calendar)
- `market: stocks`

## Baseline files

| File | Description | SHA-256 |
|---|---|---|
| `result_legacy.baseline.xlsx` | Legacy run (100/75/50/33/25% periods), filter disabled | `791B153A2063D242F913F8FE9BEBB2198FEED2BA7C2186486468414707B12DC3` |
| `result_equal_blocks.baseline.xlsx` | Equal-blocks run (7 segments), filter disabled | `29F5380CC4C71DD2139B7FB61F28A2FC18B054A2ABCC706433DBB4A2F2678006` |

> NOTE: post-rebaseline the XLSX byte-content happens to match the
> initial-capture hashes despite different in-memory metrics. This file-level
> stability is treated as a coincidence (likely numerical formatting in the
> exporter swallowing the small per-trade diffs); WP-T3 dedup is responsible
> for confirming the cause and either pinning or dissolving it. Test layer
> SHA-256 constants are anchored to the file SHA, not to engine identity.

## Canonical in-memory snapshot (legacy mode, 100% slice)

| Metric | Value |
|---|---|
| `n_bars` | 109 421 |
| `num_trades` | 4 636 |
| `sum_pnl_pct` | −312.253933 |
| `positions_sum` | 55 925 |
| `equity_curve[-1]` | 0.043687203 |
| `trades_df.shape` | (4636, 13) |
| `filter_diagnostics` | `None` (disabled path) |

Per-period (legacy, 5 slices):

| Period | n_bars | num_trades | sum_pnl_pct |
|---|---:|---:|---:|
| 100% | 109 421 | 4 636 | −312.253933 |
| 75% | 82 065 | 3 591 | −246.574952 |
| 50% | 54 710 | 2 406 | −161.784672 |
| 33% | 36 108 | 1 594 | −104.905774 |
| 25% | 27 355 | 1 236 | −85.685258 |

These constants are pinned in
`donor TESTER/tests/test_wp_t1_baseline_capture.py` (`EXPECTED_SNAPSHOT`,
`EXPECTED_100PCT`).

## How to regenerate (owner-approved only)

1. Confirm `donor/supertrend_optimizer/__init__.py` exists and `donor/` is `sys.path[0]` for the regenerator.
2. Run from repo root:
   ```bash
   python _probe_rebaseline.py     # if the temporary probe script is restored
   # or, equivalent path inside donor TESTER/:
   cd "donor TESTER"
   python run_batch_tester.py --csv data.csv --config config_tester.yaml --output-dir tests/baselines
   ```
3. Rename outputs to canonical names (`result_legacy.baseline.xlsx`, `result_equal_blocks.baseline.xlsx`).
4. Update SHA-256 hashes here AND in `tests/test_wp_t1_baseline_capture.py`.
5. Update in-memory snapshot constants in `tests/test_wp_t1_baseline_capture.py`.
6. Append a new row to the *Rebaseline history* table above with a justification.

Spec reference: Appendix A v1.1 §11.1, §17.1.1, §18
Plan reference: docs/zigzag_st_tester_phase2_implementation_plan.txt §14 WP-T1
Audit trail: docs/wp_t3_tester_dedup_log.md
