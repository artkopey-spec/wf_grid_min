from __future__ import annotations

import pickle
from types import SimpleNamespace

import numpy as np
import pytest

from supertrend_optimizer.core.volume_metrics import (
    REGIME_HIGH,
    build_volume_global_metrics,
)


@pytest.mark.slow_perf
def test_volume_runtime_pickle_roundtrip_preserves_arrays_and_readonly_flags():
    cfg = SimpleNamespace(
        mode="volume_A",
        short_window=2,
        baseline_window=4,
        threshold_ratio=1.1,
        regime_low_ratio=0.8,
        regime_high_ratio=1.2,
        direction_lookback_bars=2,
    )
    runtime = build_volume_global_metrics(
        np.array([10, 20, 30, 40, 50, 60], dtype=np.int64),
        np.array([100, 101, 102, 103, 104, 105], dtype=np.float64),
        cfg,
    )

    restored = pickle.loads(pickle.dumps(runtime))

    assert restored.reference_length == runtime.reference_length
    assert restored.absolute_offset == runtime.absolute_offset
    assert restored.filter_config_snapshot == runtime.filter_config_snapshot
    np.testing.assert_array_equal(restored.volume_regime, runtime.volume_regime)
    assert restored.volume_regime.flags.writeable is False
    with pytest.raises(ValueError):
        restored.volume_regime[0] = REGIME_HIGH
