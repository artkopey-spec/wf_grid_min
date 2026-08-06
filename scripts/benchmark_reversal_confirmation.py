"""Acceptance benchmark for reversal-confirmation runtime."""

from __future__ import annotations

import argparse
from pathlib import Path
import statistics
import subprocess
import sys
import time

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
DONOR_ROOT = REPO_ROOT / "donor"
if str(DONOR_ROOT) not in sys.path:
    sys.path.insert(0, str(DONOR_ROOT))

from supertrend_optimizer.core._reset_events import (
    TimeFilterRuntime,
    TimeWindowSchedule,
)
from supertrend_optimizer.core.reversal_confirmation_filter import (
    apply as apply_reversal,
)
from supertrend_optimizer.core.time_only_filter import apply as apply_time_only


def _fixture(n: int, edge_period: int):
    bars = np.arange(n)
    trend = np.where((bars // edge_period) % 2 == 0, 1, -1).astype(np.int8)
    index = pd.date_range("2026-01-01", periods=n, freq="min")
    inside = np.ones(n, dtype=bool)
    reset = np.zeros(n, dtype=bool)
    runtime = TimeFilterRuntime(
        reference_length=n,
        schedule=TimeWindowSchedule(((0, 24 * 60),)),
        in_window=inside,
        reset_event=reset,
    )
    return trend, index, runtime


def _measure(call, runs: int) -> tuple[float, float]:
    samples: list[float] = []
    for _ in range(runs):
        started = time.perf_counter()
        call()
        samples.append(time.perf_counter() - started)
    median = statistics.median(samples)
    cv = statistics.pstdev(samples) / statistics.mean(samples)
    return median, cv


def _peak_rss_bytes() -> int:
    """Read process peak RSS outside the measured runtime interval."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        get_memory_info = kernel32.K32GetProcessMemoryInfo
        get_memory_info.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        get_memory_info.restype = wintypes.BOOL
        process = kernel32.GetCurrentProcess()
        ok = get_memory_info(
            process,
            ctypes.byref(counters),
            counters.cb,
        )
        return int(counters.PeakWorkingSetSize) if ok else 0
    import resource

    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value * 1024 if sys.platform != "darwin" else value)


def _measure_peak_rss_in_subprocess(
    *,
    runtime_name: str,
    n: int,
    edge_period: int,
) -> int:
    output = subprocess.check_output(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--memory-worker",
            runtime_name,
            "--worker-bars",
            str(n),
            "--worker-edge-period",
            str(edge_period),
        ],
        text=True,
    )
    return int(output.strip())


def _memory_worker(runtime_name: str, n: int, edge_period: int) -> int:
    trend, index, runtime = _fixture(n, edge_period)
    if runtime_name == "time_only":
        apply_time_only(
            trend=trend,
            trade_mode="revers",
            time_filter_runtime=runtime,
            collect_filter_diagnostics=False,
        )
    elif runtime_name == "reversal_only":
        apply_reversal(
            trend=trend,
            trade_mode="revers",
            confirm_bars=2,
            index=index,
            collect_filter_diagnostics=False,
        )
    else:
        raise ValueError(f"unknown memory worker runtime: {runtime_name}")
    print(_peak_rss_bytes())
    return 0


def _linear_fit_r_squared(samples: list[tuple[int, int]]) -> float:
    sizes = np.asarray([n for n, _ in samples], dtype=np.float64)
    memory = np.asarray([rss for _, rss in samples], dtype=np.float64)
    if len(samples) < 3 or np.all(memory == memory[0]):
        return 0.0
    slope, intercept = np.polyfit(sizes, memory, 1)
    predicted = slope * sizes + intercept
    residual = float(np.sum((memory - predicted) ** 2))
    total = float(np.sum((memory - np.mean(memory)) ** 2))
    return 1.0 - residual / total if total > 0.0 and slope > 0.0 else 0.0


def run_benchmark(bars: list[int], runs: int, hard_fail: bool) -> int:
    failed = False
    timings: dict[str, list[tuple[int, float]]] = {"low": [], "medium": []}
    memory: dict[str, dict[str, list[tuple[int, int]]]] = {
        "low": {"time_only": [], "reversal_only": []},
        "medium": {"time_only": [], "reversal_only": []},
    }
    for density, edge_period in (("low", 1000), ("medium", 50)):
        for n in bars:
            trend, index, runtime = _fixture(n, edge_period)
            time_median, time_cv = _measure(
                lambda: apply_time_only(
                    trend=trend,
                    trade_mode="revers",
                    time_filter_runtime=runtime,
                    collect_filter_diagnostics=False,
                ),
                runs,
            )
            reversal_median, reversal_cv = _measure(
                lambda: apply_reversal(
                    trend=trend,
                    trade_mode="revers",
                    confirm_bars=2,
                    index=index,
                    collect_filter_diagnostics=False,
                ),
                runs,
            )
            ratio = reversal_median / time_median
            timings[density].append((n, reversal_median))
            time_peak_rss = _measure_peak_rss_in_subprocess(
                runtime_name="time_only",
                n=n,
                edge_period=edge_period,
            )
            reversal_peak_rss = _measure_peak_rss_in_subprocess(
                runtime_name="reversal_only",
                n=n,
                edge_period=edge_period,
            )
            memory[density]["time_only"].append((n, time_peak_rss))
            memory[density]["reversal_only"].append(
                (n, reversal_peak_rss)
            )
            print(
                f"{density} bars={n} time={time_median:.6f}s "
                f"reversal={reversal_median:.6f}s ratio={ratio:.3f} "
                f"cv_time={time_cv:.3f} cv_reversal={reversal_cv:.3f} "
                f"time_peak_rss_mb={time_peak_rss / (1024 * 1024):.1f} "
                f"reversal_peak_rss_mb="
                f"{reversal_peak_rss / (1024 * 1024):.1f}",
                flush=True,
            )
            failed |= ratio > 1.30
    for density, samples in timings.items():
        per_bar = [elapsed / n for n, elapsed in samples]
        spread = max(per_bar) / min(per_bar)
        linear = spread <= 2.0
        print(
            f"{density} approximate_linear_time={linear} "
            f"normalized_spread={spread:.3f}",
            flush=True,
        )
        failed |= not linear
        for runtime_name, samples in memory[density].items():
            r_squared = _linear_fit_r_squared(samples)
            memory_linear = r_squared >= 0.90
            print(
                f"{density} {runtime_name} "
                f"approximate_linear_memory={memory_linear} "
                f"r_squared={r_squared:.3f}",
                flush=True,
            )
            failed |= not memory_linear
    if failed and hard_fail:
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bars", type=int, nargs="+")
    parser.add_argument("--runs", type=int, default=7)
    parser.add_argument("--hard-fail", action="store_true")
    parser.add_argument(
        "--memory-worker",
        choices=("time_only", "reversal_only"),
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--worker-bars", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--worker-edge-period", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.memory_worker is not None:
        if args.worker_bars is None or args.worker_edge_period is None:
            parser.error("memory worker requires bars and edge period")
        return _memory_worker(
            args.memory_worker,
            args.worker_bars,
            args.worker_edge_period,
        )
    if not args.bars:
        parser.error("--bars is required")
    if args.runs < 7:
        parser.error("--runs must be >= 7")
    return run_benchmark(args.bars, args.runs, args.hard_fail)


if __name__ == "__main__":
    raise SystemExit(main())
