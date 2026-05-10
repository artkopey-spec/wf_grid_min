from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.core._block_reason import select_block_reason
from supertrend_optimizer.core._fsm_state_names import STANDALONE_VOLUME_STATE_NAMES
from supertrend_optimizer.core.backtest import RawBacktestArtifacts, run_backtest_fast
from supertrend_optimizer.core.volume_metrics import (
    BLOCK_BELOW_BASELINE,
    BLOCK_NONE,
    DIR_LONG,
    REGIME_NORMAL,
    VolumeRuntime,
)
from supertrend_optimizer.core.volume_only_filter import VolumeOnlyState, _STATE_NAMES
from supertrend_optimizer.core.zigzag_st_filter import (
    ZigZagGlobalStats,
    ZigZagPerBar,
    apply as zigzag_apply,
)
from supertrend_optimizer.engine.run import run_single_backtest
from supertrend_optimizer.testing.runner import PeriodResult, run_period
from wf_grid.collect.step_collector import collect_oos_steps
from wf_grid.wf.step_executor import StepResult, _compute_filter_diagnostics_summary


class _Volume:
    enabled = True


class _ZigZag:
    enabled = False
    daily_reset = False


class _TimeFilter:
    enabled = False


class _TradeFilter:
    enabled = True
    volume = _Volume()
    zigzag = _ZigZag()
    time_filter = _TimeFilter()


def _runtime(n: int = 32) -> VolumeRuntime:
    snapshot = {
        "volume_filter_enabled": True,
        "volume_filter_mode": "volume_A",
        "volume_short_window": 2,
        "volume_baseline_window": 3,
        "volume_threshold_ratio": 1.0,
        "volume_regime_low_ratio": 0.8,
        "volume_regime_high_ratio": 1.2,
        "volume_direction_lookback_bars": 1,
    }
    return VolumeRuntime(
        short_median_volume=np.ones(n),
        baseline_median_volume=np.ones(n),
        median_relative_volume=np.ones(n) * 1.2,
        volume_regime=np.full(n, REGIME_NORMAL, dtype=np.int8),
        volume_condition_allowed=np.ones(n, dtype=bool),
        volume_condition_block_reason=np.full(n, BLOCK_NONE, dtype=np.int8),
        volume_initial_direction=np.full(n, DIR_LONG, dtype=np.int8),
        absolute_offset=0,
        reference_length=n,
        filter_config_snapshot=snapshot,
    )


def _ohlc(n: int = 32):
    close = 100.0 + np.sin(np.linspace(0, 3 * np.pi, n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 0.5
    low = np.minimum(open_, close) - 0.5
    index = pd.date_range("2026-01-01", periods=n, freq="h")
    return open_, high, low, close, index


def _backtest_kwargs():
    return dict(
        atr_period=5,
        multiplier=1.8,
        trade_mode="both",
        commission=0.001,
        early_exit_enabled=False,
        early_exit_max_drawdown=0.5,
        early_exit_check_bars=0,
    )


def test_raw_backtest_artifacts_carries_filter_config_snapshot():
    open_, high, low, close, _index = _ohlc()
    runtime = _runtime(len(close))

    result = run_backtest_fast(
        open_,
        high,
        low,
        close,
        trade_filter_config=_TradeFilter(),
        volume_runtime=runtime,
        **_backtest_kwargs(),
    )

    assert isinstance(result, RawBacktestArtifacts)
    assert result.filter_config_snapshot is runtime.filter_config_snapshot


def test_backtest_result_carries_filter_config_snapshot():
    open_, high, low, close, index = _ohlc()
    runtime = _runtime(len(close))

    result = run_single_backtest(
        open_,
        high,
        low,
        close,
        index,
        periods_per_year=252.0,
        min_trades_required=0,
        extract_trades_flag=True,
        trade_filter_config=_TradeFilter(),
        volume_runtime=runtime,
        **_backtest_kwargs(),
    )

    assert result.filter_config_snapshot is runtime.filter_config_snapshot


def test_step_result_snapshot_field_preserves_identity():
    snapshot = {"volume_filter_enabled": True}
    sr = StepResult(
        grid_point_id="gp",
        wf_step=1,
        test_start_idx=0,
        test_end_idx=1,
        metrics={"sum_pnl_pct": 0.0, "num_trades": 0, "max_drawdown": 0.0},
        oos_trades_df=None,
        prepend_bars_requested=0,
        prepend_bars_applied=0,
        used_prepend=False,
        used_legacy_oos_path=False,
        used_defensive_fallback=False,
        oos_boundary_index=0,
        warmup_used=0,
        warmup_effective=0,
        effective_oos_bars=1,
        filter_config_snapshot=snapshot,
    )

    assert sr.filter_config_snapshot is snapshot


def test_period_result_carries_filter_config_snapshot():
    open_, high, low, close, index = _ohlc()
    runtime = _runtime(len(close))
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close},
        index=index,
    )

    result = run_period(
        df,
        atr_period=5,
        multiplier=1.8,
        trade_mode="both",
        commission=0.001,
        min_trades_required=0,
        trade_filter_config=_TradeFilter(),
        volume_runtime=runtime,
    )

    assert isinstance(result, PeriodResult)
    assert result.filter_config_snapshot is runtime.filter_config_snapshot


def test_zigzag_result_snapshot_none_without_volume_and_snapshot_with_volume():
    n = 4
    trend = np.ones(n, dtype=np.int64)
    per_bar = ZigZagPerBar(
        candidate_height_pct=np.zeros(n),
        confirm_event=np.zeros(n, dtype=np.int8),
        confirmed_leg_idx_at_t=np.full(n, -1),
        last_confirmed_leg_height_pct=np.full(n, np.nan),
        local_median_N=np.full(n, np.nan),
        local_median_available=np.zeros(n, dtype=bool),
        candidate_age_bars=np.full(n, -1),
        candidate_leg_direction=np.zeros(n, dtype=np.int8),
    )
    stats = ZigZagGlobalStats(
        reversal_threshold=0.02,
        global_stats_source="full_dataset",
        leg_height_mode="pct",
        confirmed_legs=[],
        confirmed_heights_pct=np.array([]),
        global_median=0.05,
        candidate_trigger_threshold=0.05,
        candidate_trigger_source="explicit",
        candidate_trigger_quantile=None,
        n_legs_total=0,
        insufficient_data=False,
        fail_closed_reason=None,
    )

    class _Lifecycle:
        freeze_confirmed_legs = 0

    class _Cfg:
        enabled = True
        zigzag = type("Z", (), {"local_window": 3, "daily_reset": False})()
        triggers = None
        lifecycle = _Lifecycle()
        time_filter = _TimeFilter()

    no_volume = zigzag_apply(
        trend=trend,
        trade_mode="both",
        trade_filter_config=_Cfg(),
        zigzag_global_stats=stats,
        per_bar=per_bar,
    )
    runtime = _runtime(n)
    with_volume = zigzag_apply(
        trend=trend,
        trade_mode="both",
        trade_filter_config=_Cfg(),
        zigzag_global_stats=stats,
        per_bar=per_bar,
        volume_runtime=runtime,
    )

    assert no_volume.filter_config_snapshot is None
    assert with_volume.filter_config_snapshot is runtime.filter_config_snapshot


def test_select_block_reason_uses_common_priority():
    assert select_block_reason("filter_off", "volume_below_baseline") == "volume_below_baseline"
    assert select_block_reason("daily_reset", "volume_below_baseline") == "daily_reset"
    assert (
        select_block_reason("time_filter_out_of_window", "volume_unknown_direction")
        == "time_filter_out_of_window"
    )
    assert select_block_reason("none", "volume_warmup") == "volume_warmup"


def test_standalone_volume_state_names_use_shared_constants():
    assert _STATE_NAMES[VolumeOnlyState.OFF] == STANDALONE_VOLUME_STATE_NAMES[0]
    assert _STATE_NAMES[VolumeOnlyState.ACTIVE_LONG] == STANDALONE_VOLUME_STATE_NAMES[1]
    assert _STATE_NAMES[VolumeOnlyState.ACTIVE_SHORT] == STANDALONE_VOLUME_STATE_NAMES[2]


def test_volume_summary_counters_only_when_volume_diagnostics_present():
    diag = {
        "trade_filter_state": np.array(["OFF", "ACTIVE_LONG", "ACTIVE_LONG"], dtype=object),
        "filter_block_reason": np.array(
            ["volume_below_baseline", "none", "volume_direction_warmup"],
            dtype=object,
        ),
        "volume_regime": np.array(["low_volume", "normal_volume", "high_volume"], dtype=object),
        "volume_initial_direction": np.array(["long", "long", "unknown"], dtype=object),
        "median_relative_volume": np.array([0.9, 1.1, 1.3], dtype=np.float64),
    }

    summary = _compute_filter_diagnostics_summary(diag)

    assert summary["n_volume_blocked_start_attempts"] == 2
    assert summary["n_volume_blocked_start_attempts_long"] == 1
    assert summary["n_volume_blocked_start_attempts_unknown_direction"] == 1
    assert summary["n_volume_below_baseline_blocked_start_attempts"] == 1
    assert summary["n_volume_direction_warmup_blocked_start_attempts"] == 1
    assert summary["n_volume_started_cycles"] == 1
    assert summary["avg_median_relative_volume"] == pytest.approx(1.1)


def test_step_collector_splits_zigzag_and_volume_columns():
    class _Cfg:
        status = type("Status", (), {"min_meaningful_bars": 1})()

    volume_summary = {
        "diagnostics_available": True,
        "filter_states_visited": ["OFF", "ACTIVE_LONG"],
        "n_bars_in_off": 1,
        "n_filter_blocked_entries": 1,
        "lifecycle_starts_count": 2,
        "n_volume_blocked_start_attempts": 1,
        "n_volume_below_baseline_blocked_start_attempts": 1,
        "n_volume_started_cycles": 2,
    }
    sr = StepResult(
        grid_point_id="gp",
        wf_step=1,
        test_start_idx=0,
        test_end_idx=1,
        metrics={"sum_pnl_pct": 0.0, "num_trades": 0, "max_drawdown": 0.0},
        oos_trades_df=None,
        prepend_bars_requested=0,
        prepend_bars_applied=0,
        used_prepend=False,
        used_legacy_oos_path=False,
        used_defensive_fallback=False,
        oos_boundary_index=0,
        warmup_used=0,
        warmup_effective=0,
        effective_oos_bars=10,
        filter_diagnostics_summary=volume_summary,
    )
    df = collect_oos_steps({"gp": [sr]}, _Cfg())

    assert "n_volume_blocked_start_attempts" in df.columns
    assert "lifecycle_starts_count" not in df.columns
