from __future__ import annotations

from types import SimpleNamespace
import time

import numpy as np
import pytest

from supertrend_optimizer.core.volume_metrics import build_volume_global_metrics


@pytest.mark.slow_perf
def test_volume_metrics_large_build_perf_smoke():
    n = 1_000_000
    volume = np.linspace(1_000, 50_000, n, dtype=np.float64)
    close = np.linspace(100, 500, n, dtype=np.float64)
    cfg = SimpleNamespace(
        mode="volume_A",
        short_window=50,
        baseline_window=1_000,
        threshold_ratio=1.1,
        regime_low_ratio=0.8,
        regime_high_ratio=1.2,
        direction_lookback_bars=10,
    )

    started = time.perf_counter()
    runtime = build_volume_global_metrics(volume, close, cfg)
    elapsed = time.perf_counter() - started

    assert runtime.reference_length == n
    assert elapsed < 30.0
