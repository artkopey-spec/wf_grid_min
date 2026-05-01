# WP-T3 Tester De-duplication Audit Log

> **Owner:** ZigZag ST Trade Filter Phase 2 implementation (plan v0.5.2 §14)
> **Status:** WP-T3 IN PROGRESS — file-by-file audit committed; deletions
> executed; grep / namespace gates landed. Open items tracked in §"Open
> items / follow-up" below.

## Purpose

Track every divergence between `donor/supertrend_optimizer/` (active Mode C
runtime) and `donor TESTER/supertrend_optimizer/` (legacy tester subtree),
and the resolution applied. Mandated by plan §15 #9 / WP-T3.

## 2026-04-28 — Entry 001: PACKAGING / RUNTIME SWITCH accelerated into WP-T2 unblocker

### Trigger

WP-T2 (Config schema + validation) introduced a new shared module
`donor/supertrend_optimizer/core/trade_filter_config.py` and made the
tester's `cli/tester.py` import it. The import failed with
`ModuleNotFoundError: No module named 'supertrend_optimizer.core.trade_filter_config'`
because:

* `donor TESTER/supertrend_optimizer/__init__.py` exists (regular package).
* `donor/supertrend_optimizer/__init__.py` did **not** exist (would be a
  PEP 420 namespace package).
* Python's import system, having found the regular package in
  `donor TESTER/`, never consulted `donor/`'s namespace package — submodule
  lookup is constrained to the resolved package's `__path__`.

This is **BLOCKER B-2** in plan v0.5.2 §15 #9.

### Authorized fix (owner sign-off, narrow form)

The fix is normally part of WP-T3 step 0a–0d. Owner authorized only
**steps 0a–0c** to land inside WP-T2, plus a single per-file extension to
0d for `cli/tester.py` (because `donor/cli/tester.py` already exists and
shadows the tester-side `cli/tester.py` after the resolution flip). Specifically:

| Step | Action | Status |
|---|---|---|
| 0a | Create `donor/supertrend_optimizer/__init__.py` (regular package, version mirror of tester subtree) | DONE |
| 0b | Enforce `donor/` precedes `donor TESTER/` in `sys.path` (`donor TESTER/tests/conftest.py`) | DONE |
| 0c | Smoke test: `supertrend_optimizer.__file__` resolves under `donor/` | DONE — `donor TESTER/tests/test_wp_t2_packaging_smoke.py` (8 assertions) |
| 0d (narrow) | Move WP-T2 trade_filter parsing to `donor/.../cli/tester.py` (canonical runtime copy). `donor TESTER/.../cli/tester.py` is left in place — NOT deleted, NOT shimmed. | DONE in WP-T2; **deleted in WP-T3 Entry 002** |

### Side-effect: implicit engine swap

The WP-T2 unblocker also flipped the runtime for **all** `supertrend_optimizer.*`
submodules to `donor/`. The two donor subtrees are NOT bit-equal: see
Entry 002's full file-by-file audit table.

### Knock-on: WP-T1 baseline invalidated

The original WP-T1 baseline (in-memory snapshot constants and XLSX hashes)
was captured against `donor TESTER/`'s stub engine. After the unblocker
flipped runtime to `donor/`, the in-memory metrics changed:

| Metric (legacy 100%) | WP-T1 stale (`donor TESTER/`) | Rebaseline (`donor/`) | Δ |
|---|---:|---:|---:|
| `num_trades` | 4639 | 4636 | −3 |
| `sum_pnl_pct` | −312.4305 | −312.253933 | +0.18% |
| `positions_sum` | 55894 | 55925 | +31 |
| `equity_curve[-1]` | 0.043611 | 0.043687203 | +0.18% |
| `trades_df.shape` | (4639, 13) | (4636, 13) | −3 rows |

Owner authorized **variant 1**: re-capture baseline against the canonical
Mode C runtime. Updated artifacts:

* `donor TESTER/tests/baselines/result_legacy.baseline.xlsx` (regenerated)
* `donor TESTER/tests/baselines/result_equal_blocks.baseline.xlsx` (regenerated)
* `donor TESTER/tests/baselines/README.md` (engine source, rebaseline history, snapshot table)
* `donor TESTER/tests/test_wp_t1_baseline_capture.py` (`EXPECTED_SNAPSHOT`, `EXPECTED_100PCT` constants)

### Anomaly noted

Despite the in-memory metrics changing, the **XLSX SHA-256 hashes did not
change** between the stale and rebaselined captures:

* `result_legacy.baseline.xlsx`: `791B153A2063D242F913F8FE9BEBB2198FEED2BA7C2186486468414707B12DC3` (unchanged)
* `result_equal_blocks.baseline.xlsx`: `29F5380CC4C71DD2139B7FB61F28A2FC18B054A2ABCC706433DBB4A2F2678006` (unchanged)

Hypothesis: the XLSX exporter rounds / formats values such that the per-trade
diffs do not propagate to bytes. WP-T3 Entry 002 dedup notes that
`io/excel_tester.py` is hash-equal between the two donor subtrees, which
explains the byte stability — same exporter applied to slightly different
inputs converges to identical XLSX in this dataset.

## 2026-04-28 — Entry 002: WP-T3 dedup pass (engine + core + tester-only duplicates)

### Audit method

* file-level SHA-256 + line-count diff between `donor/supertrend_optimizer/`
  and `donor TESTER/supertrend_optimizer/`;
* caller-graph search (`from supertrend_optimizer.*` resolution after WP-T2
  unblocker = always `donor/`);
* literal scan for forbidden tokens (`CLOSE_TO_CLOSE`, `compute_zigzag_global_stats`,
  `'allow_entry'` / `'filtered_reason'` / `'zz_st_*'`).

### File-by-file audit + decision

Scope per owner directive: `engine/`, `core/`, `testing/`, `io/`, `cli/`.
`data/` and `utils/` are explicitly out of WP-T3 owner scope and are tracked
under "Open items / follow-up" below. Top-level
`donor TESTER/supertrend_optimizer/__init__.py` is KEPT — runtime never
resolves through it (donor/ wins, gated by `test_wp_t2_packaging_smoke.py`)
but it remains as a documentation marker of the legacy tester subtree.

| Path (relative to `donor TESTER/supertrend_optimizer/`) | donor size | TESTER size | SHA match | Decision | Reason |
|---|---:|---:|---|---|---|
| `engine/run.py` | 20099 | 13240 | ❌ | **DELETED** | donor/ has full filter integration; TESTER stub lacks `trade_filter_config` parameters and `filter_diagnostics` writes |
| `engine/result.py` | 9516 | 4767 | ❌ | **DELETED** | donor/ has `BacktestResult.filter_diagnostics: Optional[FilterDiagnostics]` + length-invariant `__post_init__`; TESTER stub absent |
| `engine/__init__.py` | 242 | 242 | ✅ | **DELETED** | empty package marker; meaningless after run.py / result.py removal |
| `core/backtest.py` | 17626 | 13209 | ❌ | **DELETED** | donor/ has `run_backtest_fast` filter wiring; TESTER stub returns 7-tuple without filter |
| `core/trades.py` | 15377 | 13866 | ❌ | **DELETED** | donor/ canonical (Phase 1 / Phase 2); TESTER stub diverged |
| `core/metrics.py` | 22851 | 19520 | ❌ | **DELETED** | donor/ canonical |
| `core/calculator.py` | 11624 | 8885 | ❌ | **DELETED** | donor/ canonical |
| `core/__init__.py` | 1362 | 1362 | ✅ | **DELETED** | exports point at deleted submodules — would break import-time |
| `core/zigzag_st_filter.py` | 68085 | absent | n/a | **DONOR-ONLY** (no action) | Phase 2 ZigZag ST FSM lives only in donor/ by design (plan §13) |
| `testing/runner.py` | 24082 | 24086 | ❌ | **DELETED** | only docstring diff (stale `CLOSE_TO_CLOSE` mentions in TESTER copy violate §15 #2); donor/ canonical |
| `testing/signal_events.py` | 12283 | 12283 | ✅ | **DELETED** | exact byte-equal duplicate; runtime always resolves to donor/ |
| `testing/__init__.py` | 47 | 47 | ✅ | **DELETED** | empty package marker |
| `io/excel_tester.py` | 38714 | 38714 | ✅ | **DELETED** | exact byte-equal duplicate |
| `io/excel_format_helpers.py` | 9119 | 9119 | ✅ | **DELETED** | exact byte-equal duplicate |
| `cli/tester.py` | 25405 | 25087 | ❌ | **DELETED** | donor/ canonical (carries WP-T2 + WP-T3 caller_pipeline whitelist); TESTER copy was a temporary mirror per WP-T2 owner instruction |
| `cli/__init__.py` | 49 | 49 | ✅ | **DELETED** | empty package marker after `cli/tester.py` removal |
| `__init__.py` (top-level) | 1185 | 88 | ❌ | **KEPT** | legacy package marker; runtime never resolves through it (top-level winner is donor/, gated by `test_wp_t2_packaging_smoke.py`) |
| `data/loader.py`, `data/validator.py`, `data/timeframe.py`, `data/__init__.py` | various | various | ❌ | **KEPT (deferred)** | out of WP-T3 owner scope per directive #1; runtime does not resolve through them — see "Open items" |
| `utils/enums.py`, `utils/exceptions.py`, `utils/warmup.py`, `utils/constants.py`, `utils/config.py`, `utils/time_utils.py`, `utils/__init__.py` | various | various | mixed | **KEPT (deferred)** | out of WP-T3 owner scope per directive #1 — see "Open items" |

**Total deletions: 15 files** (engine: 3, core: 5, testing: 3, io: 2, cli: 2).

### Surface contract after deletion

| Symbol resolved by `import supertrend_optimizer....` | File | Resolves from |
|---|---|---|
| `supertrend_optimizer` (top-level) | `__init__.py` | donor/ |
| `supertrend_optimizer.engine.run` | `engine/run.py` | donor/ |
| `supertrend_optimizer.engine.result` | `engine/result.py` | donor/ |
| `supertrend_optimizer.core.backtest` | `core/backtest.py` | donor/ |
| `supertrend_optimizer.core.zigzag_st_filter` | `core/zigzag_st_filter.py` | donor/ (only there by design) |
| `supertrend_optimizer.core.trade_filter_config` | `core/trade_filter_config.py` | donor/ (Mode C single source of truth) |
| `supertrend_optimizer.cli.tester` | `cli/tester.py` | donor/ |
| `supertrend_optimizer.testing.runner` | `testing/runner.py` | donor/ |
| `supertrend_optimizer.testing.signal_events` | `testing/signal_events.py` | donor/ |
| `supertrend_optimizer.io.excel_tester` | `io/excel_tester.py` | donor/ |
| `supertrend_optimizer.io.excel_format_helpers` | `io/excel_format_helpers.py` | donor/ |
| `supertrend_optimizer.data.*` | (various) | donor/ — TESTER copies remain on disk but unused |
| `supertrend_optimizer.utils.*` | (various) | donor/ — TESTER copies remain on disk but unused |

`donor TESTER/run_batch_tester.py` (NOT inside `supertrend_optimizer/`) is
unchanged in WP-T3; it imports from the canonical `supertrend_optimizer.*`
namespace and therefore consumes donor/.

### Gates landed

* `donor TESTER/tests/conftest.py` — module-level runtime assert: BOTH
  `supertrend_optimizer` top-level AND `supertrend_optimizer.engine.run`
  resolve under `donor/`. Required by plan WP-T3 step 5 (extended in
  audit-fix v0.5.1 to check both levels).
* `donor TESTER/tests/test_phase2_tester_namespace_resolution.py` — formal
  test #27 covering Mode-C namespace contract (top-level + engine.run +
  engine.result + core.backtest + core.zigzag_st_filter + core.trade_filter_config
  all resolve from donor/).
* `donor TESTER/tests/test_phase2_tester_static_grep_gate.py` — formal
  test #29 covering forbidden literals in scoped tester paths (see test
  docstring for scope rationale).
* `donor/supertrend_optimizer/core/trade_filter_config.py` — caller_pipeline
  whitelist for `zigzag_st_mode`: allowed = `{"wf_grid", "tester"}`,
  any other caller is a `ConfigError`. Plan §5.5.

### Anomaly resolved (Entry 001 anomaly)

`io/excel_tester.py` and `io/excel_format_helpers.py` were confirmed
hash-equal between donor/ and donor TESTER/. The XLSX-byte-stability
reported in Entry 001 is therefore explained: identical exporter applied
to (slightly different) backtest inputs collapses to byte-equal XLSX in
this dataset because the differing per-trade fields round to the same
formatted cells under openpyxl's default precision. WP-T3 leaves this
as informational; the in-memory snapshot remains the behaviour-sensitive
gate.

## 2026-04-28 — Entry 003: production-entrypoint bootstrap (owner audit-fix v0.5.2)

### Issue surfaced post-Entry-002

After WP-T3 dedup deleted `donor TESTER/supertrend_optimizer/cli/tester.py`,
the production CLI `donor TESTER/run_batch_tester.py` started failing OUTSIDE
pytest:

```text
$ cd "donor TESTER"
$ python run_batch_tester.py --help
ModuleNotFoundError: No module named 'supertrend_optimizer.cli.tester'
```

Root cause: when invoking `python run_batch_tester.py`, Python sets
`sys.path[0]` to the script's directory (`donor TESTER/`).  That directory
still contains the legacy package marker
`donor TESTER/supertrend_optimizer/__init__.py` (KEPT decision in Entry 002),
so `supertrend_optimizer` resolves to the legacy subtree which no longer
ships `cli/tester.py` (deleted in Entry 002).

`donor TESTER/tests/conftest.py` already had the corrected `sys.path`
ordering for pytest sessions, which is why the test suite stayed green —
masking the production breakage.

### Fix

In-script `sys.path` bootstrap added at the top of `run_batch_tester.py`,
**before** any `from supertrend_optimizer.*` import.  Mirrors the conftest
logic so production CLI and pytest use identical resolution order
(`donor/` then `donor TESTER/`).

The bootstrap:

* validates that `donor/supertrend_optimizer/__init__.py` exists (fail-fast
  with a clear `RuntimeError` if the active donor is missing);
* dedupes-then-inserts both roots so it is idempotent under module re-import;
* runs a defensive `__file__`-based assert that the resolved
  `supertrend_optimizer` is the donor copy (catches PYTHONPATH overrides
  and stale installs).

### Smoke gate added

`donor TESTER/tests/test_phase2_tester_entrypoint_bootstrap.py` (8 tests):

| Group | Tests | What it pins |
|---|---:|---|
| `TestEntrypointFileExists` | 3 | entrypoint + active donor present; legacy `cli/tester.py` stays deleted (else bootstrap would not be needed) |
| `TestProductionEntrypointBootstrap` | 4 | runs the CLI via `subprocess.run([python, run_batch_tester.py, --help])` from BOTH `donor TESTER/` cwd AND repo root cwd; asserts exit 0 + argparse usage; pins absence of `ModuleNotFoundError`; pins diagnostic substrings in the bootstrap source |
| `TestImportContractFromSubprocess` | 1 | runs a fresh-interpreter probe that imports the entrypoint module + every WP-T3 surface symbol and asserts each resolves under `donor/` — independent of pytest conftest |

`PYTHONPATH` is stripped from the subprocess env so the gate reflects only
the in-script bootstrap, not whatever the developer's shell happens to
contain.

### Why this is a separate gate from `test_phase2_tester_namespace_resolution.py`

The namespace-resolution test runs under pytest, where `conftest.py`
manipulates `sys.path` BEFORE any test module is collected.  That correctly
exercises the import contract for the test harness, but it is blind to
the production-CLI invocation path (`python script.py`) — which is exactly
the path Entry 003 broke.  Subprocess invocation closes the gap.

### Files touched

* `donor TESTER/run_batch_tester.py` — bootstrap block + `# noqa: E402`
  on subsequent third-party / package imports;
* `donor TESTER/tests/test_phase2_tester_entrypoint_bootstrap.py` — new.

### Verification

```text
donor TESTER/tests:                             181 passed
wf_grid trade_filter regressions (wp2 + wp3):    90 passed
manual repro `cd "donor TESTER" && python run_batch_tester.py --help`: exit 0
```

## Open items / follow-up

| Item | Owner | Why not in WP-T3 |
|---|---|---|
| `donor TESTER/supertrend_optimizer/data/{loader,validator,timeframe,__init__}.py` cleanup | follow-up cleanup PR | Out of WP-T3 owner audit scope (directive #1 limited to engine/core/testing/io/cli). All four are stale stubs (donor/ versions are 2.5–3× larger and canonical); runtime never resolves through them after the unblocker. |
| `donor TESTER/supertrend_optimizer/utils/{enums,exceptions,warmup,constants,config,time_utils,__init__}.py` cleanup | follow-up cleanup PR | Same scope rationale as `data/`. Critical: `utils/enums.py` still defines `ExecutionModel.CLOSE_TO_CLOSE` which violates §15 #2 — but it is unreachable code. The static grep gate (test #29) excludes `data/` and `utils/` with documented rationale; on cleanup these directories must be deleted. |
| `donor TESTER/supertrend_optimizer/__init__.py` removal | follow-up cleanup PR | Same as above — purely documentary at this point; deletion is a single-file follow-up. |
| Empty leaf directories (`engine/`, `core/`, `testing/`, `io/`, `cli/` after deletion) | git auto-prune | git tracks files, not directories; empty dirs disappear from any clone. Local working copies may retain them. |

## Plan references

* `docs/zigzag_st_tester_phase2_implementation_plan.txt` §3.1 (Mode C definition)
* `docs/zigzag_st_tester_phase2_implementation_plan.txt` §13 (file impact table)
* `docs/zigzag_st_tester_phase2_implementation_plan.txt` §14 WP-T1 / WP-T3
* `docs/zigzag_st_tester_phase2_implementation_plan.txt` §15 #2 (CLOSE_TO_CLOSE retired)
* `docs/zigzag_st_tester_phase2_implementation_plan.txt` §15 #9 (BLOCKER B-2)
* `docs/zigzag_st_trade_filter_spec_v1_1.txt` §11.1 (disabled-path baseline contract)
