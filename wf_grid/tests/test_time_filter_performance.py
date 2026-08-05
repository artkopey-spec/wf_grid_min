from __future__ import annotations

import gc
import pickle
import time
import tracemalloc

import pandas as pd
import pytest

from supertrend_optimizer.core._reset_events import (
    TimeWindowSchedule,
    build_time_filter_runtime,
)


SCHEDULE = TimeWindowSchedule(((9 * 60, 12 * 60), (16 * 60, 18 * 60)))


@pytest.mark.slow_perf
@pytest.mark.parametrize("n", [100_000, 500_000])
def test_time_filter_runtime_performance_and_ipc_acceptance(n: int) -> None:
    """Plan v3 section 21 reference acceptance on an already-built index."""
    index = pd.date_range("2025-01-01", periods=n, freq="min")

    # Warm imports/vectorized pandas paths before taking the reference reading.
    build_time_filter_runtime(index, n, enabled=True, schedule=SCHEDULE)
    gc.collect()
    tracemalloc.start()
    started = time.perf_counter()
    runtime = build_time_filter_runtime(
        index,
        n,
        enabled=True,
        schedule=SCHEDULE,
    )
    elapsed = time.perf_counter() - started
    _, peak_temporary_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    runtime_array_bytes = runtime.in_window.nbytes + runtime.reset_event.nbytes
    payload = pickle.dumps(runtime, protocol=pickle.HIGHEST_PROTOCOL)

    assert elapsed <= 1.0
    assert peak_temporary_bytes <= 32 * 1024 * 1024
    assert runtime_array_bytes <= 3 * n
    assert len(payload) <= 3 * n + 64 * 1024


def test_time_filter_slice_reuses_schedule_without_rebuilding_timestamps() -> None:
    index = pd.date_range("2025-01-01", periods=100_000, freq="min")
    runtime = build_time_filter_runtime(
        index,
        len(index),
        enabled=True,
        schedule=SCHEDULE,
    )

    sliced = runtime.slice(10_000, 90_000)

    assert sliced.schedule is runtime.schedule
    assert sliced.reference_length == 80_000
    assert sliced.reset_event[0] == 0

