from __future__ import annotations

import numpy as np
import pytest

from supertrend_optimizer.core.backtest import generate_positions
from supertrend_optimizer.core.position_target import target_from_trend


@pytest.mark.parametrize("mode", ["revers", "both", "long", "short"])
def test_target_helper_preserves_generate_positions(mode: str) -> None:
    trend = np.asarray([0, 1, -1, 1, 0, -1], dtype=np.int64)
    expected = generate_positions(trend, mode)
    target = target_from_trend(trend, mode)
    observed = np.zeros(len(target), dtype=np.int8)
    observed[1:] = target[:-1]
    np.testing.assert_array_equal(observed, expected)
    assert target.dtype == np.int8


def test_target_helper_validates_shape_and_mode() -> None:
    with pytest.raises(ValueError, match="1-D"):
        target_from_trend(np.zeros((2, 2)), "revers")
    with pytest.raises(ValueError, match="trade_mode"):
        target_from_trend(np.zeros(2), "invalid")
