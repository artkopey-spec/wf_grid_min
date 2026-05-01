"""
Тест-защита от O(L²) сложности в _build_causal_statistics и
_broadcast_stats_to_bars (план §3.1, FIX 2).

При удвоении L время роста должно быть < 3× (O(L log L)).
O(L²) дало бы ~4×.

Помечен как @pytest.mark.slow — запускать через: pytest -m slow
"""
from __future__ import annotations

import time

import numpy as np
import pytest

from supertrend_optimizer.core.zigzag_filter import (
    LEG_DIR_DOWN,
    LEG_DIR_UP,
    _PartialLeg,
    _build_causal_statistics,
    _broadcast_stats_to_bars,
)


def _mk_partial_leg(leg_id: int, confirm_bar: int, height_pct: float = 0.05) -> _PartialLeg:
    direction = LEG_DIR_UP if leg_id % 2 == 0 else LEG_DIR_DOWN
    start_price = 100.0
    end_price = start_price * (1.0 + height_pct) if direction == LEG_DIR_UP else start_price * (1.0 - height_pct)
    start_bar = max(0, confirm_bar - 5)
    end_bar = confirm_bar - 1
    return _PartialLeg(
        leg_id=leg_id,
        start_bar=start_bar,
        end_bar=end_bar,
        confirm_bar=confirm_bar,
        start_price=start_price,
        end_price=end_price,
        direction=direction,
        height_pct=height_pct,
        length_bars=end_bar - start_bar,
        confirm_lag_bars=1,
    )


def _run_stats(L: int) -> float:
    rng = np.random.default_rng(42)
    heights = rng.uniform(0.01, 0.20, L)
    legs = [
        _mk_partial_leg(i, confirm_bar=i * 3 + 2, height_pct=float(heights[i]))
        for i in range(L)
    ]
    t0 = time.perf_counter()
    snapshots = _build_causal_statistics(legs, k_local=5, q_strong=0.80)
    n_bars = legs[-1].confirm_bar + 10 if legs else 10
    _broadcast_stats_to_bars(legs, snapshots, n_bars=n_bars, k_local=5, q_strong=0.80)
    return time.perf_counter() - t0


@pytest.mark.slow
def test_stats_complexity_scaling():
    """
    При удвоении L время не должно расти квадратично.
    O(L log L): ratio ≈ 2.1; O(L²): ratio ≈ 4+
    """
    _ = _run_stats(200)  # прогрев JIT/кешей

    t1 = _run_stats(2000)
    t2 = _run_stats(4000)

    ratio = t2 / t1 if t1 > 0 else 0.0
    assert ratio < 3.5, (
        f"Подозрение на O(L²): при удвоении L с 2000 до 4000 "
        f"время выросло в {ratio:.2f}× (ожидалось < 3.5×). "
        f"t(2000)={t1*1000:.1f}ms, t(4000)={t2*1000:.1f}ms"
    )
