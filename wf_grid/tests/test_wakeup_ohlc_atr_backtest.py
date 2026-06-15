from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from supertrend_optimizer.core.backtest import run_backtest_fast
from supertrend_optimizer.core.zigzag_st_filter import ZigZagGlobalStats
from supertrend_optimizer.utils.enums import ExecutionModel


def _close(n: int = 80) -> np.ndarray:
    x = np.linspace(0.0, 6.0 * np.pi, n, dtype=np.float64)
    return 100.0 + np.sin(x) * 2.0 + np.linspace(0.0, 1.0, n)


def _open_from_close(close: np.ndarray) -> np.ndarray:
    open_prices = np.roll(close, 1)
    open_prices[0] = close[0]
    return open_prices


def _mode_d_atr_config() -> SimpleNamespace:
    return SimpleNamespace(
        enabled=True,
        type="zigzag_st_mode",
        zigzag=SimpleNamespace(
            enabled=True,
            local_window=5,
        ),
        volume=SimpleNamespace(enabled=False),
        lifecycle=SimpleNamespace(
            freeze_confirmed_legs=0,
            exit_off_mode="exit C",
            exit_b_immediate_off=False,
        ),
        wakeup_regime=SimpleNamespace(
            enabled=True,
            lock_cycle_direction=False,
            entry=SimpleNamespace(
                candidate_height=SimpleNamespace(enabled=False),
                candidate_age=SimpleNamespace(enabled=False),
                atr_expansion=SimpleNamespace(
                    enabled=True,
                    short_window=2,
                    long_window=5,
                    min_ratio=1.0,
                ),
                volume_expansion=SimpleNamespace(enabled=False),
            ),
            exit=SimpleNamespace(
                ttl=SimpleNamespace(enabled=False, bars=10),
                no_fresh_candidate=SimpleNamespace(enabled=False),
                action=SimpleNamespace(mode="block_new_entries"),
            ),
        ),
    )


def _mode_d_stats() -> ZigZagGlobalStats:
    return ZigZagGlobalStats(
        reversal_threshold=0.01,
        global_stats_source="full_dataset",
        leg_height_mode="pct",
        confirmed_legs=[],
        confirmed_heights_pct=np.array([], dtype=np.float64),
        global_median=0.02,
        candidate_trigger_threshold=0.01,
        candidate_trigger_source="explicit",
        candidate_trigger_quantile=None,
        n_legs_total=0,
        insufficient_data=False,
        fail_closed_reason=None,
        metadata={},
        zigzag_mode="D",
        wakeup_entry_candidate_height_threshold=None,
        wakeup_no_fresh_candidate_height_threshold=None,
    )


def _run_backtest(
    *,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
):
    return run_backtest_fast(
        open_prices=_open_from_close(close),
        high=high,
        low=low,
        close=close,
        atr_period=5,
        multiplier=2.0,
        trade_mode="both",
        commission=0.0,
        early_exit_enabled=False,
        early_exit_max_drawdown=0.5,
        early_exit_check_bars=0,
        execution_model=ExecutionModel.OPEN_TO_OPEN,
        trade_filter_config=_mode_d_atr_config(),
        zigzag_global_stats=_mode_d_stats(),
    )


def test_run_backtest_fast_mode_d_atr_uses_real_ohlc_and_completes():
    close = _close()
    high = close + np.linspace(0.5, 2.0, len(close))
    low = close - np.linspace(0.4, 1.5, len(close))

    result = _run_backtest(high=high, low=low, close=close)

    diag = result.filter_diagnostics
    assert diag is not None
    assert "wakeup_entry_atr_ratio" in diag
    assert len(diag["wakeup_entry_atr_ratio"]) == len(result.positions)
    assert np.isfinite(diag["wakeup_entry_atr_ratio"][4:]).any()


def test_run_backtest_fast_high_low_changes_atr_ratio_not_zigzag_diagnostics():
    close = _close()
    narrow_high = close + 0.5
    narrow_low = close - 0.5
    width = np.linspace(0.5, 4.0, len(close))
    wide_high = close + width
    wide_low = close - width * 0.75

    narrow = _run_backtest(high=narrow_high, low=narrow_low, close=close)
    wide = _run_backtest(high=wide_high, low=wide_low, close=close)

    diag_n = narrow.filter_diagnostics
    diag_w = wide.filter_diagnostics
    assert diag_n is not None and diag_w is not None
    ratio_n = diag_n["wakeup_entry_atr_ratio"]
    ratio_w = diag_w["wakeup_entry_atr_ratio"]
    finite = np.isfinite(ratio_n) & np.isfinite(ratio_w)
    assert finite.any()
    assert np.any(np.abs(ratio_n[finite] - ratio_w[finite]) > 1e-12)

    np.testing.assert_array_equal(
        diag_n["candidate_height_pct"],
        diag_w["candidate_height_pct"],
    )
    np.testing.assert_array_equal(
        diag_n["local_median_N"],
        diag_w["local_median_N"],
    )
