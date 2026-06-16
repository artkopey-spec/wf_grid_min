from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.core.backtest import run_backtest_fast
from supertrend_optimizer.core.filter_trade_diagnostics import (
    attach_trade_filter_diagnostics,
)
from supertrend_optimizer.core.volume_metrics import (
    BLOCK_BELOW_BASELINE,
    BLOCK_NONE,
    BLOCK_WARMUP,
    DIR_LONG,
    DIR_SHORT,
    DIR_UNKNOWN,
    REGIME_NORMAL,
    REGIME_WARMUP,
    VolumeRuntime,
)
from supertrend_optimizer.core.volume_only_filter import apply as volume_apply
from supertrend_optimizer.utils.enums import ExecutionModel
from supertrend_optimizer.utils.exceptions import ConfigError


@dataclass
class _VolumeCfg:
    enabled: bool = True
    mode: str = "volume_A"
    daily_reset: bool = False
    cycle_direction_gate: bool = False


@dataclass
class _ZigZagCfg:
    enabled: bool = False
    daily_reset: bool = False


@dataclass
class _Lifecycle:
    exit_off_mode: str = "exit B"
    exit_off_zz_leg_count: int = 99
    exit_b_immediate_off: bool = True


@dataclass
class _TimeFilter:
    enabled: bool = False


@dataclass
class _TradeFilter:
    enabled: bool = True
    zigzag: _ZigZagCfg | None = field(default_factory=_ZigZagCfg)
    volume: _VolumeCfg | None = field(default_factory=_VolumeCfg)
    lifecycle: _Lifecycle = field(default_factory=_Lifecycle)
    time_filter: _TimeFilter = field(default_factory=_TimeFilter)


def _runtime(
    *,
    n: int = 6,
    mode: str = "volume_A",
    threshold: float = 1.0,
    direction: int | np.ndarray = DIR_LONG,
    allowed: bool | np.ndarray = True,
    relative: float | np.ndarray = 1.2,
    block_reason: int | np.ndarray = BLOCK_NONE,
    regime: int | np.ndarray = REGIME_NORMAL,
    lookback: int = 1,
) -> VolumeRuntime:
    direction_arr = _array(direction, n, np.int8)
    allowed_arr = _array(allowed, n, bool)
    relative_arr = _array(relative, n, np.float64)
    block_reason_arr = _array(block_reason, n, np.int8)
    regime_arr = _array(regime, n, np.int8)
    snapshot = {
        "volume_filter_enabled": True,
        "volume_filter_mode": mode,
        "volume_short_window": 2,
        "volume_baseline_window": 3,
        "volume_threshold_ratio": threshold,
        "volume_regime_low_ratio": 0.8,
        "volume_regime_high_ratio": 1.2,
        "volume_direction_lookback_bars": lookback,
    }
    return VolumeRuntime(
        short_median_volume=np.ones(n, dtype=np.float64),
        baseline_median_volume=np.ones(n, dtype=np.float64),
        median_relative_volume=relative_arr,
        volume_regime=regime_arr,
        volume_condition_allowed=allowed_arr,
        volume_condition_block_reason=block_reason_arr,
        volume_initial_direction=direction_arr,
        absolute_offset=0,
        reference_length=n,
        filter_config_snapshot=snapshot,
    )


def _array(value, n: int, dtype):
    if np.isscalar(value):
        return np.full(n, value, dtype=dtype)
    return np.asarray(value, dtype=dtype)


def _run(
    *,
    trade_mode: str = "both",
    trend: np.ndarray | None = None,
    runtime: VolumeRuntime | None = None,
    cfg: _TradeFilter | None = None,
    daily_reset_event: np.ndarray | None = None,
    time_filter_events: tuple[np.ndarray, np.ndarray] | None = None,
    index: pd.Index | None = None,
    collect_filter_diagnostics: bool = True,
):
    n = runtime.reference_length if runtime is not None else 6
    if trend is None:
        trend = np.ones(n, dtype=np.int64)
    close = np.linspace(100.0, 105.0, n, dtype=np.float64)
    open_prices = close.copy()
    return volume_apply(
        open_prices=open_prices,
        close=close,
        trend=trend,
        trade_mode=trade_mode,
        trade_filter_config=cfg or _TradeFilter(),
        volume_runtime=runtime or _runtime(n=n),
        execution_model=ExecutionModel.OPEN_TO_OPEN,
        index=index,
        daily_reset_event=daily_reset_event,
        time_filter_events=time_filter_events,
        collect_filter_diagnostics=collect_filter_diagnostics,
    )


@pytest.mark.parametrize(
    ("trade_mode", "direction", "expected"),
    [
        ("long", DIR_LONG, 1),
        ("short", DIR_SHORT, -1),
        ("both", DIR_LONG, 1),
        ("revers", DIR_SHORT, -1),
    ],
)
def test_start_by_volume_direction_and_trade_mode(trade_mode, direction, expected):
    result = _run(
        trade_mode=trade_mode,
        runtime=_runtime(direction=direction),
    )

    assert result.positions[1] == expected
    assert result.filter_diagnostics["trade_filter_state"][0] in {
        "ACTIVE_LONG",
        "ACTIVE_SHORT",
    }


@pytest.mark.parametrize(
    ("mode", "relative"),
    [
        ("volume_A", np.array([1.2, 1.2, 0.8, 0.8, 0.8, 0.8])),
        ("volume_B", np.array([0.8, 0.8, 1.2, 1.2, 1.2, 1.2])),
    ],
)
def test_volume_reversal_in_both_modes(mode, relative):
    result = _run(runtime=_runtime(mode=mode, relative=relative, threshold=1.0))

    assert result.filter_diagnostics["trade_filter_state"][2] == "OFF"
    assert result.filter_diagnostics["filter_block_reason"][2] == "volume_reversal"
    assert result.positions[3] == 0


@pytest.mark.parametrize(
    ("mode", "threshold", "exit_threshold", "relative"),
    [
        ("volume_A", 2.2, 1.8, np.array([2.3, 2.0, 1.7, 1.7, 1.7, 1.7])),
        ("volume_B", 0.8, 1.0, np.array([0.7, 0.9, 1.1, 1.1, 1.1, 1.1])),
    ],
)
def test_volume_reversal_uses_exit_hysteresis_threshold(
    mode, threshold, exit_threshold, relative
):
    runtime = _runtime(mode=mode, relative=relative, threshold=threshold)
    runtime.filter_config_snapshot["volume_exit_hysteresis_ratio"] = exit_threshold

    result = _run(runtime=runtime)

    assert result.filter_diagnostics["trade_filter_state"][1] != "OFF"
    assert result.filter_diagnostics["trade_filter_state"][2] == "OFF"
    assert result.filter_diagnostics["filter_block_reason"][2] == "volume_reversal"


def test_old_volume_runtime_snapshot_falls_back_to_threshold_and_no_freeze():
    runtime = _runtime(
        mode="volume_A",
        threshold=1.0,
        relative=np.array([1.2, 1.2, 0.8, 0.8, 0.8, 0.8]),
    )
    assert "volume_exit_hysteresis_ratio" not in runtime.filter_config_snapshot
    assert "volume_exit_freeze_bars" not in runtime.filter_config_snapshot

    result = _run(runtime=runtime)

    assert result.filter_diagnostics["filter_block_reason"][2] == "volume_reversal"
    assert result.filter_diagnostics["trade_filter_state"][2] == "OFF"


@pytest.mark.parametrize(
    ("exit_freeze_bars", "expected_exit_bar"),
    [
        (0, 1),
        (1, 1),
        (2, 2),
    ],
)
def test_volume_exit_freeze_off_by_one(exit_freeze_bars, expected_exit_bar):
    runtime = _runtime(
        mode="volume_A",
        threshold=1.0,
        relative=np.array([1.2, 0.8, 0.8, 0.8, 0.8, 0.8]),
    )
    runtime.filter_config_snapshot["volume_exit_freeze_bars"] = exit_freeze_bars

    result = _run(runtime=runtime)

    assert result.filter_diagnostics["filter_block_reason"][expected_exit_bar] == "volume_reversal"
    assert result.filter_diagnostics["trade_filter_state"][expected_exit_bar] == "OFF"
    for t in range(1, expected_exit_bar):
        assert result.filter_diagnostics["filter_block_reason"][t] != "volume_reversal"
        assert result.filter_diagnostics["trade_filter_state"][t] != "OFF"


def test_hard_exits_bypass_volume_exit_freeze():
    runtime = _runtime(
        mode="volume_A",
        threshold=1.0,
        relative=np.array([1.2, 0.8, 0.8, 0.8, 0.8, 0.8]),
    )
    runtime.filter_config_snapshot["volume_exit_freeze_bars"] = 10

    daily = _run(
        runtime=runtime,
        daily_reset_event=np.array([0, 1, 0, 0, 0, 0], dtype=bool),
    )
    forced = _run(
        trade_mode="long",
        trend=np.array([1, -1, -1, -1, -1, -1], dtype=np.int64),
        runtime=runtime,
    )

    assert daily.filter_diagnostics["filter_block_reason"][1] == "daily_reset"
    assert daily.filter_diagnostics["trade_filter_state"][1] == "OFF"
    assert forced.filter_diagnostics["filter_block_reason"][1] == "trade_mode_forced_exit"
    assert forced.filter_diagnostics["trade_filter_state"][1] == "OFF"


def test_freeze_blocks_reversal_but_allows_same_bar_st_flip():
    runtime = _runtime(
        mode="volume_A",
        threshold=1.0,
        relative=np.array([1.2, 0.8, 0.8, 0.8, 0.8, 0.8]),
        direction=DIR_LONG,
    )
    runtime.filter_config_snapshot["volume_exit_freeze_bars"] = 10

    result = _run(
        trade_mode="both",
        trend=np.array([1, -1, -1, -1, -1, -1], dtype=np.int64),
        runtime=runtime,
    )

    assert result.filter_diagnostics["filter_block_reason"][1] == "none"
    assert result.filter_diagnostics["trade_filter_state"][1] == "ACTIVE_SHORT"
    assert result.positions[2] == -1


def test_direction_warmup_blocks_start():
    result = _run(
        runtime=_runtime(
            direction=np.array([DIR_UNKNOWN, DIR_LONG, DIR_LONG, DIR_LONG, DIR_LONG, DIR_LONG]),
            lookback=2,
        )
    )

    assert result.positions[1] == 0
    assert result.filter_diagnostics["filter_block_reason"][0] == "volume_direction_warmup"


def test_equal_close_momentum_unknown_direction_blocks_start():
    result = _run(
        runtime=_runtime(
            direction=np.array([DIR_LONG, DIR_LONG, DIR_UNKNOWN, DIR_LONG, DIR_LONG, DIR_LONG]),
            lookback=1,
        ),
        daily_reset_event=np.array([0, 1, 0, 0, 0, 0], dtype=bool),
    )

    assert result.positions[3] == 0
    assert result.filter_diagnostics["filter_block_reason"][2] == "volume_unknown_direction"


def test_direction_warmup_uses_volume_runtime_absolute_offset_for_slices():
    full_runtime = _runtime(
        n=80,
        direction=np.full(80, DIR_UNKNOWN, dtype=np.int8),
        lookback=20,
    )

    past_global_warmup = _run(runtime=full_runtime.slice(50, 60))
    inside_global_warmup = _run(runtime=full_runtime.slice(8, 14))

    assert (
        past_global_warmup.filter_diagnostics["filter_block_reason"][5]
        == "volume_unknown_direction"
    )
    assert (
        inside_global_warmup.filter_diagnostics["filter_block_reason"][2]
        == "volume_direction_warmup"
    )


def test_trade_mode_disallows_direction_blocks_start():
    result = _run(
        trade_mode="long",
        runtime=_runtime(direction=DIR_SHORT),
    )

    assert np.all(result.positions == 0)
    assert (
        result.filter_diagnostics["filter_block_reason"][0]
        == "volume_trade_mode_disallowed_direction"
    )


def test_cycle_direction_gate_false_preserves_legacy_volume_only_behavior():
    result = _run(
        trade_mode="long",
        trend=np.array([1, 1, -1, -1, -1, -1], dtype=np.int64),
        runtime=_runtime(direction=DIR_LONG),
        cfg=_TradeFilter(volume=_VolumeCfg(cycle_direction_gate=False)),
    )

    assert result.filter_diagnostics["trade_filter_state"][0] == "ACTIVE_LONG"
    assert result.filter_diagnostics["trade_filter_state"][2] == "OFF"
    assert result.filter_diagnostics["filter_block_reason"][2] == "trade_mode_forced_exit"
    assert result.filter_diagnostics["cycle_initial_direction"][0] == "long"
    assert result.filter_diagnostics["cycle_direction_gate_enabled"][0] == 0
    assert result.filter_diagnostics["cycle_direction_gate_passed"][0] == 1
    assert result.filter_diagnostics["cycle_initial_direction"][2] == "unknown"
    assert result.filter_diagnostics["cycle_direction_gate_passed"][2] == 0
    assert result.positions.tolist() == [0, 1, 1, 0, 1, 1]

    wrong_direction = _run(
        trade_mode="long",
        runtime=_runtime(direction=DIR_SHORT),
        cfg=_TradeFilter(volume=_VolumeCfg(cycle_direction_gate=False)),
    )

    assert np.all(wrong_direction.positions == 0)
    assert wrong_direction.filter_diagnostics["trade_filter_state"][0] == "OFF"
    assert (
        wrong_direction.filter_diagnostics["filter_block_reason"][0]
        == "volume_trade_mode_disallowed_direction"
    )


def test_cycle_direction_gate_long_mode_short_cycle_stays_suppressed():
    result = _run(
        trade_mode="long",
        runtime=_runtime(
            direction=np.array(
                [DIR_SHORT, DIR_SHORT, DIR_LONG, DIR_LONG, DIR_LONG, DIR_LONG],
                dtype=np.int8,
            )
        ),
        cfg=_TradeFilter(volume=_VolumeCfg(cycle_direction_gate=True)),
    )

    diag = result.filter_diagnostics
    assert diag["trade_filter_state"].tolist() == ["SUPPRESSED_SHORT"] * 6
    assert diag["filter_block_reason"].tolist() == [
        "volume_cycle_direction_mismatch"
    ] * 6
    assert diag["cycle_initial_direction"].tolist() == ["short"] * 6
    np.testing.assert_array_equal(
        diag["cycle_direction_gate_enabled"], np.ones(6, dtype=np.int8)
    )
    np.testing.assert_array_equal(
        diag["cycle_direction_gate_passed"], np.zeros(6, dtype=np.int8)
    )
    np.testing.assert_array_equal(result.positions, np.zeros(6, dtype=np.int8))


def test_cycle_direction_gate_forbidden_opposite_flip_does_not_forced_exit():
    result = _run(
        trade_mode="long",
        trend=np.array([1, 1, -1, -1, -1, -1], dtype=np.int64),
        runtime=_runtime(direction=DIR_LONG),
        cfg=_TradeFilter(volume=_VolumeCfg(cycle_direction_gate=True)),
    )

    diag = result.filter_diagnostics
    assert diag["trade_filter_state"][2] == "ACTIVE_LONG"
    assert diag["filter_block_reason"][2] == "volume_cycle_direction_mismatch"
    assert "trade_mode_forced_exit" not in set(diag["filter_block_reason"].tolist())
    assert result.positions.tolist() == [0, 1, 1, 1, 1, 1]


def test_cycle_direction_gate_suppressed_cycle_ignores_all_st_flips():
    result = _run(
        trade_mode="long",
        trend=np.array([1, -1, 1, -1, 1, -1], dtype=np.int64),
        runtime=_runtime(direction=DIR_SHORT),
        cfg=_TradeFilter(volume=_VolumeCfg(cycle_direction_gate=True)),
    )

    diag = result.filter_diagnostics
    assert diag["trade_filter_state"].tolist() == ["SUPPRESSED_SHORT"] * 6
    assert diag["cycle_initial_direction"].tolist() == ["short"] * 6
    assert diag["filter_block_reason"].tolist() == [
        "volume_cycle_direction_mismatch"
    ] * 6
    np.testing.assert_array_equal(result.positions, np.zeros(6, dtype=np.int8))


def test_cycle_direction_gate_unknown_does_not_start_cycle_then_short_suppresses():
    result = _run(
        trade_mode="long",
        runtime=_runtime(
            direction=np.array(
                [DIR_UNKNOWN, DIR_SHORT, DIR_SHORT, DIR_SHORT, DIR_SHORT, DIR_SHORT],
                dtype=np.int8,
            ),
            lookback=1,
        ),
        cfg=_TradeFilter(volume=_VolumeCfg(cycle_direction_gate=True)),
    )

    diag = result.filter_diagnostics
    assert diag["trade_filter_state"][0] == "OFF"
    assert diag["filter_block_reason"][0] == "volume_direction_warmup"
    assert diag["cycle_initial_direction"][0] == "unknown"
    assert diag["trade_filter_state"][1] == "SUPPRESSED_SHORT"
    assert diag["cycle_initial_direction"][1] == "short"
    np.testing.assert_array_equal(result.positions, np.zeros(6, dtype=np.int8))


def test_cycle_direction_gate_reversal_resets_suppressed_cycle_and_new_long_can_start():
    result = _run(
        trade_mode="long",
        runtime=_runtime(
            direction=np.array(
                [DIR_SHORT, DIR_SHORT, DIR_LONG, DIR_LONG, DIR_LONG, DIR_LONG],
                dtype=np.int8,
            ),
            relative=np.array([1.2, 1.2, 0.8, 1.2, 1.2, 1.2], dtype=np.float64),
        ),
        cfg=_TradeFilter(volume=_VolumeCfg(cycle_direction_gate=True)),
    )

    diag = result.filter_diagnostics
    assert diag["trade_filter_state"][0] == "SUPPRESSED_SHORT"
    assert diag["trade_filter_state"][2] == "OFF"
    assert diag["cycle_initial_direction"][2] == "unknown"
    assert diag["filter_block_reason"][2] == "volume_reversal"
    assert diag["trade_filter_state"][3] == "ACTIVE_LONG"
    assert diag["cycle_initial_direction"][3] == "long"
    assert diag["cycle_direction_gate_passed"][3] == 1
    assert result.positions.tolist() == [0, 0, 0, 0, 1, 1]


def test_cycle_direction_gate_short_mode_forbidden_long_flip_does_not_forced_exit():
    result = _run(
        trade_mode="short",
        trend=np.array([-1, -1, 1, 1, 1, 1], dtype=np.int64),
        runtime=_runtime(direction=DIR_SHORT),
        cfg=_TradeFilter(volume=_VolumeCfg(cycle_direction_gate=True)),
    )

    diag = result.filter_diagnostics
    assert diag["trade_filter_state"][2] == "ACTIVE_SHORT"
    assert diag["filter_block_reason"][2] == "volume_cycle_direction_mismatch"
    assert "trade_mode_forced_exit" not in set(diag["filter_block_reason"].tolist())
    assert result.positions.tolist() == [0, -1, -1, -1, -1, -1]


@pytest.mark.parametrize(
    ("trade_mode", "direction", "trend", "expected_state", "expected_positions"),
    [
        (
            "both",
            DIR_LONG,
            np.array([1, 1, -1, -1, -1, -1], dtype=np.int64),
            "ACTIVE_LONG",
            [0, 1, 1, 1, 1, 1],
        ),
        (
            "revers",
            DIR_SHORT,
            np.array([-1, -1, 1, 1, 1, 1], dtype=np.int64),
            "ACTIVE_SHORT",
            [0, -1, -1, -1, -1, -1],
        ),
    ],
)
def test_cycle_direction_gate_both_and_revers_cannot_flip_opposite_to_cycle(
    trade_mode,
    direction,
    trend,
    expected_state,
    expected_positions,
):
    result = _run(
        trade_mode=trade_mode,
        trend=trend,
        runtime=_runtime(direction=direction),
        cfg=_TradeFilter(volume=_VolumeCfg(cycle_direction_gate=True)),
    )

    diag = result.filter_diagnostics
    assert diag["trade_filter_state"][2] == expected_state
    assert diag["filter_block_reason"][2] == "volume_cycle_direction_mismatch"
    assert result.positions.tolist() == expected_positions


@pytest.mark.parametrize("trade_mode", ["both", "revers"])
def test_st_flip_switches_position_in_both_and_revers(trade_mode):
    result = _run(
        trade_mode=trade_mode,
        trend=np.array([1, 1, -1, -1, -1, -1], dtype=np.int64),
        runtime=_runtime(direction=DIR_LONG),
    )

    assert result.filter_diagnostics["trade_filter_state"][2] == "ACTIVE_SHORT"
    assert result.positions[3] == -1


@pytest.mark.parametrize("trade_mode", ["both", "revers"])
def test_gate_false_cycle_initial_direction_stays_latched_after_allowed_flip(
    trade_mode,
):
    result = _run(
        trade_mode=trade_mode,
        trend=np.array([1, 1, -1, -1, -1, -1], dtype=np.int64),
        runtime=_runtime(
            direction=DIR_LONG,
            relative=np.array([1.2, 1.2, 1.2, 1.2, 0.8, 1.2], dtype=np.float64),
        ),
        cfg=_TradeFilter(volume=_VolumeCfg(cycle_direction_gate=False)),
    )

    diag = result.filter_diagnostics
    assert diag["trade_filter_state"][0] == "ACTIVE_LONG"
    assert diag["trade_filter_state"][2] == "ACTIVE_SHORT"
    assert diag["filter_block_reason"][4] == "volume_reversal"
    assert diag["cycle_initial_direction"].tolist() == [
        "long",
        "long",
        "long",
        "long",
        "unknown",
        "long",
    ]


@pytest.mark.parametrize(
    ("trade_mode", "direction", "trend"),
    [
        ("long", DIR_LONG, np.array([1, 1, -1, -1, -1, -1], dtype=np.int64)),
        ("short", DIR_SHORT, np.array([-1, -1, 1, 1, 1, 1], dtype=np.int64)),
    ],
)
def test_st_flip_forced_exit_in_long_and_short_modes(trade_mode, direction, trend):
    result = _run(
        trade_mode=trade_mode,
        trend=trend,
        runtime=_runtime(direction=direction),
    )

    assert result.filter_diagnostics["trade_filter_state"][2] == "OFF"
    assert result.filter_diagnostics["filter_block_reason"][2] == "trade_mode_forced_exit"
    assert result.positions[3] == 0


def test_daily_reset_priority_over_reversal_and_start():
    result = _run(
        runtime=_runtime(
            relative=np.array([1.2, 1.2, 0.8, 1.2, 1.2, 1.2], dtype=np.float64)
        ),
        daily_reset_event=np.array([0, 0, 1, 0, 0, 0], dtype=bool),
    )

    assert result.filter_diagnostics["filter_block_reason"][2] == "daily_reset"
    assert result.positions[3] == 0


def test_volume_daily_reset_is_inferred_from_datetime_index():
    index = pd.date_range("2025-01-01 23:58", periods=6, freq="min")

    result = _run(
        cfg=_TradeFilter(volume=_VolumeCfg(daily_reset=True)),
        index=index,
    )

    diag = result.filter_diagnostics
    np.testing.assert_array_equal(
        diag["daily_reset_enabled"], np.ones(6, dtype=np.int8)
    )
    np.testing.assert_array_equal(
        diag["daily_reset_event"], np.array([0, 0, 1, 0, 0, 0], dtype=np.int8)
    )
    assert diag["filter_block_reason"][2] == "daily_reset"


def test_volume_daily_reset_requires_datetime_index():
    with pytest.raises(ConfigError, match="trade_filter.volume.daily_reset"):
        _run(cfg=_TradeFilter(volume=_VolumeCfg(daily_reset=True)))


def test_time_filter_reset_priority_over_reversal_and_start():
    n = 6
    result = _run(
        runtime=_runtime(
            relative=np.array([1.2, 1.2, 0.8, 1.2, 1.2, 1.2], dtype=np.float64)
        ),
        time_filter_events=(
            np.ones(n, dtype=bool),
            np.array([0, 0, 1, 0, 0, 0], dtype=bool),
        ),
    )

    assert result.filter_diagnostics["filter_block_reason"][2] == "time_filter_reset"
    assert result.positions[3] == 0


def test_lifecycle_exit_off_keys_are_inert():
    runtime = _runtime(direction=DIR_LONG)
    with_lifecycle = _run(runtime=runtime, cfg=_TradeFilter(lifecycle=_Lifecycle()))
    without_lifecycle = _run(
        runtime=runtime,
        cfg=_TradeFilter(lifecycle=_Lifecycle(exit_off_mode="exit A", exit_b_immediate_off=False)),
    )

    np.testing.assert_array_equal(with_lifecycle.positions, without_lifecycle.positions)
    np.testing.assert_array_equal(
        with_lifecycle.filter_diagnostics["trade_filter_state"],
        without_lifecycle.filter_diagnostics["trade_filter_state"],
    )


def test_volume_categorical_diagnostics_are_strings_not_int8():
    result = _run(
        runtime=_runtime(
            direction=np.array([DIR_UNKNOWN, DIR_LONG, DIR_LONG, DIR_LONG, DIR_LONG, DIR_LONG]),
            allowed=np.array([False, True, True, True, True, True]),
            block_reason=np.array([BLOCK_WARMUP, BLOCK_NONE, BLOCK_NONE, BLOCK_NONE, BLOCK_NONE, BLOCK_NONE]),
            regime=np.array([REGIME_WARMUP, REGIME_NORMAL, REGIME_NORMAL, REGIME_NORMAL, REGIME_NORMAL, REGIME_NORMAL]),
            lookback=2,
        )
    )
    diag = result.filter_diagnostics

    for key in (
        "volume_regime",
        "volume_condition_block_reason",
        "volume_initial_direction",
    ):
        assert diag[key].dtype == object
        assert isinstance(diag[key][0], str)
    assert diag["volume_condition_allowed"].dtype == bool


def test_reset_diagnostics_are_exported_for_trade_attachment():
    daily_reset = np.array([0, 0, 1, 0, 0, 0], dtype=bool)
    result = _run(daily_reset_event=daily_reset)
    diag = result.filter_diagnostics

    assert "daily_reset_event" in diag
    assert "time_filter_reset_event" in diag
    assert "time_filter_in_window" in diag
    assert "time_filter_enabled" in diag
    np.testing.assert_array_equal(
        diag["daily_reset_event"], daily_reset.astype(np.int8)
    )


def test_standalone_volume_trade_exit_reason_daily_reset():
    result = _run(daily_reset_event=np.array([0, 0, 1, 0, 0, 0], dtype=bool))
    trades = pd.DataFrame(
        {
            "entry_index": [1],
            "exit_index": [3],
        }
    )

    enriched = attach_trade_filter_diagnostics(
        trades, result.filter_diagnostics
    )

    assert enriched["exit_reason"].iloc[0] == "filter_daily_reset"


def test_standalone_volume_trade_exit_reason_time_reset():
    n = 6
    result = _run(
        time_filter_events=(
            np.ones(n, dtype=bool),
            np.array([0, 0, 1, 0, 0, 0], dtype=bool),
        )
    )
    trades = pd.DataFrame(
        {
            "entry_index": [1],
            "exit_index": [3],
        }
    )

    enriched = attach_trade_filter_diagnostics(
        trades, result.filter_diagnostics
    )

    assert enriched["exit_reason"].iloc[0] == "filter_time_reset"


def test_filter_config_snapshot_is_volume_runtime_snapshot_object():
    runtime = _runtime()
    result = _run(runtime=runtime)

    assert result.filter_config_snapshot is runtime.filter_config_snapshot


def test_volume_only_collect_filter_diagnostics_false_returns_none_and_same_positions():
    runtime = _runtime(
        n=8,
        direction=np.array([
            DIR_LONG,
            DIR_LONG,
            DIR_SHORT,
            DIR_SHORT,
            DIR_LONG,
            DIR_LONG,
            DIR_SHORT,
            DIR_SHORT,
        ]),
        allowed=np.array([True, True, True, False, True, True, True, True]),
        block_reason=np.array([
            BLOCK_NONE,
            BLOCK_NONE,
            BLOCK_NONE,
            BLOCK_BELOW_BASELINE,
            BLOCK_NONE,
            BLOCK_NONE,
            BLOCK_NONE,
            BLOCK_NONE,
        ]),
        relative=np.array([1.2, 1.1, 0.7, 0.6, 1.3, 1.2, 0.9, 0.8]),
    )
    trend = np.array([1, 1, -1, -1, 1, 1, -1, -1], dtype=np.int64)

    enabled = _run(runtime=runtime, trend=trend, collect_filter_diagnostics=True)
    disabled = _run(runtime=runtime, trend=trend, collect_filter_diagnostics=False)

    np.testing.assert_array_equal(disabled.positions, enabled.positions)
    assert disabled.filter_diagnostics is None
    assert disabled.filter_config_snapshot is runtime.filter_config_snapshot
    assert enabled.filter_diagnostics is not None


def test_volume_condition_fail_blocks_start():
    result = _run(
        runtime=_runtime(
            direction=DIR_LONG,
            allowed=False,
            block_reason=BLOCK_BELOW_BASELINE,
        )
    )

    assert np.all(result.positions == 0)
    assert result.filter_diagnostics["filter_block_reason"][0] == "volume_below_baseline"


def test_dispatcher_disabled_path_is_bit_identical_without_filter():
    open_, high, low, close = _ohlc()
    kwargs = dict(
        atr_period=5,
        multiplier=1.8,
        trade_mode="revers",
        commission=0.001,
        early_exit_enabled=False,
        early_exit_max_drawdown=0.5,
        early_exit_check_bars=0,
    )
    disabled_cfg = _TradeFilter(enabled=False)

    baseline = run_backtest_fast(open_, high, low, close, trade_filter_config=None, **kwargs)
    disabled = run_backtest_fast(
        open_, high, low, close, trade_filter_config=disabled_cfg, **kwargs
    )

    np.testing.assert_array_equal(disabled.positions, baseline.positions)
    np.testing.assert_array_equal(disabled.returns, baseline.returns)
    np.testing.assert_array_equal(disabled.equity_curve, baseline.equity_curve)
    assert baseline.filter_diagnostics is None
    assert disabled.filter_diagnostics is None


def test_dispatcher_standalone_volume_requires_runtime():
    open_, high, low, close = _ohlc()

    with pytest.raises(
        RuntimeError,
        match="volume_runtime required when trade_filter.volume.enabled=true",
    ):
        run_backtest_fast(
            open_,
            high,
            low,
            close,
            atr_period=5,
            multiplier=1.8,
            trade_mode="both",
            commission=0.001,
            early_exit_enabled=False,
            early_exit_max_drawdown=0.5,
            early_exit_check_bars=0,
            trade_filter_config=_TradeFilter(),
        )


def test_dispatcher_zigzag_requires_global_stats():
    open_, high, low, close = _ohlc()
    cfg = _TradeFilter(zigzag=_ZigZagCfg(enabled=True), volume=_VolumeCfg(enabled=False))

    with pytest.raises(
        RuntimeError,
        match="zigzag_global_stats required when trade_filter.zigzag.enabled=true",
    ):
        run_backtest_fast(
            open_,
            high,
            low,
            close,
            atr_period=5,
            multiplier=1.8,
            trade_mode="both",
            commission=0.001,
            early_exit_enabled=False,
            early_exit_max_drawdown=0.5,
            early_exit_check_bars=0,
            trade_filter_config=cfg,
        )


def test_dispatcher_rejects_enabled_filter_without_subfilters():
    open_, high, low, close = _ohlc()
    cfg = _TradeFilter(
        zigzag=_ZigZagCfg(enabled=False),
        volume=_VolumeCfg(enabled=False),
    )

    with pytest.raises(
        RuntimeError,
        match="at least one trade subfilter must be enabled",
    ):
        run_backtest_fast(
            open_,
            high,
            low,
            close,
            atr_period=5,
            multiplier=1.8,
            trade_mode="both",
            commission=0.001,
            early_exit_enabled=False,
            early_exit_max_drawdown=0.5,
            early_exit_check_bars=0,
            trade_filter_config=cfg,
        )


def test_dispatcher_routes_standalone_volume_filter():
    open_, high, low, close = _ohlc()
    runtime = _runtime(n=len(close), direction=DIR_LONG)

    result = run_backtest_fast(
        open_,
        high,
        low,
        close,
        atr_period=5,
        multiplier=1.8,
        trade_mode="both",
        commission=0.001,
        early_exit_enabled=False,
        early_exit_max_drawdown=0.5,
        early_exit_check_bars=0,
        trade_filter_config=_TradeFilter(),
        volume_runtime=runtime,
    )

    assert result.filter_diagnostics is not None
    assert result.filter_diagnostics["trade_filter_state"][0] == "ACTIVE_LONG"
    assert result.positions[1] == 1


def test_dispatcher_volume_only_collect_filter_diagnostics_false():
    open_, high, low, close = _ohlc()
    runtime = _runtime(n=len(close), direction=DIR_LONG)
    kwargs = dict(
        atr_period=5,
        multiplier=1.8,
        trade_mode="both",
        commission=0.001,
        early_exit_enabled=False,
        early_exit_max_drawdown=0.5,
        early_exit_check_bars=0,
        trade_filter_config=_TradeFilter(),
        volume_runtime=runtime,
    )

    enabled = run_backtest_fast(open_, high, low, close, **kwargs)
    disabled = run_backtest_fast(
        open_,
        high,
        low,
        close,
        **kwargs,
        collect_filter_diagnostics=False,
    )

    np.testing.assert_array_equal(disabled.positions, enabled.positions)
    assert disabled.filter_diagnostics is None
    assert disabled.filter_config_snapshot is runtime.filter_config_snapshot
    assert enabled.filter_diagnostics is not None


def test_volume_only_filter_does_not_import_zigzag_filter_module():
    source = Path("donor/supertrend_optimizer/core/volume_only_filter.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)

    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert "supertrend_optimizer.core.zigzag_st_filter" not in imported_modules


def _ohlc(n: int = 24):
    close = 100.0 + np.sin(np.linspace(0, 4 * np.pi, n)) * 2.0
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 0.5
    low = np.minimum(open_, close) - 0.5
    return open_, high, low, close
