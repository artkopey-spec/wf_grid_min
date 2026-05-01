"""
Parallel-execution benchmark for WF Grid (plan Phase 7 / step 12).

Run the full pipeline against config.yaml in four modes and report wall time,
wf_grid_execution timing, key stage timings, and peak RSS (parent + workers).

Usage
-----
    python scripts/benchmark_parallel.py
    python scripts/benchmark_parallel.py --config config.yaml
    python scripts/benchmark_parallel.py --runs 2   # repeat each mode N times

Output is printed to stdout.  The pipeline also writes XLSX files to the
output directory (auto-generated next to config.yaml by default); remove
them manually after benchmarking if storage space matters.

Historical baseline (first recorded numbers)
--------------------------------------------
Machine  : Intel Core i3-1215U (12th Gen), 8 logical CPUs
Python   : 3.13.13
pandas   : 2.3.3
OS       : Windows 10 (build 26200), x64
Config   : config.yaml  (273 grid points x 7 WF steps)
Date     : 2026-04-29
Note     : psutil not installed; RSS column shows 0.00 MB (install psutil for real values)

Mode                       wall_s   wf_grid_exec_s   speedup
sequential                 22.18    19.69            1.00x
parallel max_workers=2     14.86    12.27            1.49x
parallel max_workers=4     13.13    10.73            1.69x
parallel max_workers=8     11.52     9.14            1.93x

Notes
-----
- chunksize tuning is omitted until benchmark shows submit overhead > 5% of
  wf_grid_execution (plan §7 note).
- rss_peak_delta_MB = peak(parent + all child workers) RSS during the run
  minus RSS before the run.  Workers appear in children() only while the
  ProcessPoolExecutor context is open; on Windows the pool is closed before
  run_grid_pipeline returns, so the peak sampler captures the maximum during
  execution rather than the final RSS.
- psutil is required for RSS measurements.  Install with: pip install psutil
"""

from __future__ import annotations

import os
import sys
import time
import threading
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap: put project root and donor on sys.path so that both wf_grid and
# supertrend_optimizer are importable, same as run.py.
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
_DONOR_ROOT = _PROJECT_ROOT / "donor"

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_DONOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_DONOR_ROOT))


# ---------------------------------------------------------------------------
# RSS helpers
# ---------------------------------------------------------------------------

def _psutil_available() -> bool:
    try:
        import psutil  # noqa: F401
        return True
    except ImportError:
        return False


def _rss_total_mb() -> float:
    """RSS of current process + all live child processes, in MB.

    On Windows, ProcessPoolExecutor worker processes appear as children of
    the main process while the pool is open.  Summing parent + children gives
    a realistic view of total memory cost during a parallel run.
    Returns 0.0 if psutil is unavailable or if children cannot be enumerated.
    """
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        rss = proc.memory_info().rss
        for child in proc.children(recursive=True):
            try:
                rss += child.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return rss / (1024 * 1024)
    except Exception:
        return 0.0


class _RssSampler:
    """Background-thread RSS sampler that records peak(parent + children) MB.

    Usage::

        sampler = _RssSampler(interval_s=0.25)
        sampler.start()
        # ... run pipeline ...
        sampler.stop()
        peak = sampler.peak_mb
    """

    def __init__(self, interval_s: float = 0.25) -> None:
        self._interval = interval_s
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.peak_mb: float = 0.0

    def start(self) -> None:
        self.peak_mb = _rss_total_mb()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()

    def _sample_loop(self) -> None:
        while not self._stop_event.wait(self._interval):
            current = _rss_total_mb()
            if current > self.peak_mb:
                self.peak_mb = current

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        # One final sample after pool is closed.
        current = _rss_total_mb()
        if current > self.peak_mb:
            self.peak_mb = current


# ---------------------------------------------------------------------------
# Single benchmark run
# ---------------------------------------------------------------------------

def _run_one(
    run_grid_pipeline,
    config_path: str,
    label: str,
    parallel_enabled: bool,
    max_workers: int | None,
) -> dict[str, Any]:
    """Execute one benchmark mode and return a result dict."""
    rss_before_mb = _rss_total_mb()

    sampler = _RssSampler(interval_s=0.25)
    sampler.start()
    t_wall_start = time.perf_counter()

    result = run_grid_pipeline(
        config_path=config_path,
        parallel_enabled=parallel_enabled,
        max_workers=max_workers,
    )

    wall_s = time.perf_counter() - t_wall_start
    sampler.stop()
    rss_after_mb = _rss_total_mb()

    if result.error:
        return {
            "label": label,
            "error": result.error,
            "wall_s": wall_s,
        }

    timings = result.diagnostics.timings if result.diagnostics else {}
    diag = result.diagnostics

    return {
        "label": label,
        "error": None,
        "wall_s": wall_s,
        "execution_mode": result.execution_mode,
        "wf_grid_execution_s": timings.get("wf_grid_execution"),
        "collection_s": timings.get("collection"),
        "scoring_s": timings.get("scoring"),
        "ranking_s": timings.get("ranking"),
        "xlsx_export_s": timings.get("xlsx_export"),
        "grid_size": getattr(diag, "grid_size", None),
        "n_wf_steps": getattr(diag, "n_wf_steps", None),
        "rss_before_mb": rss_before_mb,
        "rss_after_mb": rss_after_mb,
        "rss_peak_mb": sampler.peak_mb,
        "rss_peak_delta_mb": sampler.peak_mb - rss_before_mb,
    }


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _fmt(v: Any, unit: str = "s", width: int = 8) -> str:
    if v is None:
        return f"{'n/a':>{width + len(unit)}}"
    return f"{v:{width}.2f}{unit}"


def _print_results(results: list[dict[str, Any]]) -> None:
    header = (
        f"\n{'Mode':<35} {'wall':>9}  {'wf_exec':>9}  "
        f"{'collect':>9}  {'scoring':>9}  {'ranking':>9}  "
        f"{'xlsx_exp':>9}  {'rss_peak_dMB':>14}"
    )
    sep = "-" * len(header)
    print(header)
    print(sep)
    for r in results:
        if r.get("error"):
            print(f"  {r['label']:<33}  ERROR: {r['error']}")
            continue
        print(
            f"  {r['label']:<33} "
            f"{_fmt(r['wall_s'], width=7)} "
            f"{_fmt(r.get('wf_grid_execution_s'), width=7)} "
            f"{_fmt(r.get('collection_s'), width=7)} "
            f"{_fmt(r.get('scoring_s'), width=7)} "
            f"{_fmt(r.get('ranking_s'), width=7)} "
            f"{_fmt(r.get('xlsx_export_s'), width=7)} "
            f"{_fmt(r.get('rss_peak_delta_mb'), unit='MB', width=8)}"
        )
    print(sep)

    # Speedup relative to sequential.
    seq = next(
        (r for r in results if not r.get("error") and r.get("execution_mode") == "sequential"),
        None,
    )
    if seq and seq["wall_s"] > 0:
        print("\nSpeedup vs sequential (wall time):")
        for r in results:
            if r.get("error") or r["label"] == seq["label"]:
                continue
            speedup = seq["wall_s"] / r["wall_s"]
            print(f"  {r['label']:<35}: {speedup:.2f}x")

    if not _psutil_available():
        print(
            "\n  [note] psutil not installed - RSS values are 0.  "
            "Install with: pip install psutil"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

MODES: list[tuple[str, bool, int | None]] = [
    ("sequential",             False, None),
    ("parallel max_workers=2", True,  2),
    ("parallel max_workers=4", True,  4),
    ("parallel max_workers=8", True,  8),
]


def main() -> None:
    import argparse
    import io

    # Force UTF-8 output so Unicode chars in labels survive narrow terminal codepages.
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Benchmark parallel vs sequential WF Grid execution.",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to YAML config (default: config.yaml).",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Repetitions per mode (default: 1).  Use --runs 2 for warm-cache pass.",
    )
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=["sequential", "2", "4", "8"],
        default=None,
        help="Run only specific modes.  E.g. --modes sequential 2 4",
    )
    args = parser.parse_args()

    config_path = str(Path(args.config).resolve())
    if not Path(config_path).exists():
        print(f"[ERROR] Config not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    from wf_grid.pipeline.orchestrator import run_grid_pipeline

    # Filter modes if requested.
    modes = MODES
    if args.modes is not None:
        _keep = set(args.modes)
        modes = [
            m for m in MODES
            if (m[0] == "sequential" and "sequential" in _keep)
            or (m[1] and str(m[2]) in _keep)
        ]

    print(f"Benchmark: {config_path}")
    print(f"Modes:     {[m[0] for m in modes]}")
    print(f"Runs/mode: {args.runs}")
    if not _psutil_available():
        print("[note] psutil not installed - RSS values will be 0 (pip install psutil)")

    all_results: list[dict[str, Any]] = []

    for run_idx in range(1, args.runs + 1):
        if args.runs > 1:
            print(f"\n=== Run {run_idx}/{args.runs} ===")
        run_results: list[dict[str, Any]] = []
        for label, par_enabled, n_workers in modes:
            print(f"  Running: {label} ...", end="", flush=True)
            r = _run_one(
                run_grid_pipeline,
                config_path=config_path,
                label=label,
                parallel_enabled=par_enabled,
                max_workers=n_workers,
            )
            run_results.append(r)
            status = "ERROR" if r.get("error") else f"{r['wall_s']:.1f}s"
            print(f" {status}")

        _print_results(run_results)
        all_results.extend(run_results)

    # If multiple runs, also print best (min wall) per mode.
    if args.runs > 1:
        print("\n=== Best wall time per mode (across all runs) ===")
        seen: dict[str, dict] = {}
        for r in all_results:
            if r.get("error"):
                continue
            lbl = r["label"]
            if lbl not in seen or r["wall_s"] < seen[lbl]["wall_s"]:
                seen[lbl] = r
        _print_results(list(seen.values()))


if __name__ == "__main__":
    main()
