from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pytest

from supertrend_optimizer.core.volume_metrics import (
    BLOCK_BELOW_BASELINE,
    BLOCK_NONE,
    DIR_LONG,
    REGIME_NORMAL,
    VolumeRuntime,
)
from supertrend_optimizer.core.zigzag_st_filter import (
    ZigZagGlobalStats,
    ZigZagPerBar,
    apply,
)


@dataclass
class _Toggle:
    enabled: bool = True


@dataclass
class _Triggers:
    candidate_threshold: _Toggle = field(default_factory=_Toggle)
    confirmed_median: _Toggle = field(default_factory=_Toggle)


@dataclass
class _ZigZagCfg:
    local_window: int = 3
    daily_reset: bool = False


@dataclass
class _Lifecycle:
    freeze_confirmed_legs: int = 0
    exit_off_mode: str = "exit A"
    exit_off_zz_leg_count: int | None = None
    exit_b_immediate_off: bool = False


@dataclass
class _TimeFilter:
    enabled: bool = False


@dataclass
class _TradeFilter:
    enabled: bool = True
    zigzag: _ZigZagCfg = field(default_factory=_ZigZagCfg)
    triggers: _Triggers = field(default_factory=_Triggers)
    lifecycle: _Lifecycle = field(default_factory=_Lifecycle)
    time_filter: _TimeFilter = field(default_factory=_TimeFilter)


def _stats(mode: str) -> ZigZagGlobalStats:
    return ZigZagGlobalStats(
        reversal_threshold=0.02,
        global_stats_source="full_dataset",
        leg_height_mode="pct",
        confirmed_legs=[],
        confirmed_heights_pct=np.array([], dtype=np.float64),
        global_median=0.05,
        candidate_trigger_threshold=0.05,
        candidate_trigger_source="explicit",
        candidate_trigger_quantile=None,
        n_legs_total=0,
        insufficient_data=False,
        fail_closed_reason=None,
        zigzag_mode=mode,
    )


def _per_bar(
    *,
    mode: str,
    n: int,
    trigger_t: int = 1,
    zigzag_signal: bool = True,
) -> ZigZagPerBar:
    candidate_height = np.zeros(n, dtype=np.float64)
    confirm_event = np.zeros(n, dtype=np.int8)
    local_median = np.full(n, np.nan, dtype=np.float64)
    local_median_available = np.zeros(n, dtype=bool)
    candidate_age = np.full(n, -1, dtype=np.int64)
    candidate_direction = np.zeros(n, dtype=np.int8)

    if zigzag_signal and mode in {"A", "C", "A+B", "C+B"}:
        candidate_height[trigger_t] = 0.10
        candidate_age[trigger_t] = 2
        candidate_direction[trigger_t] = DIR_LONG
    if zigzag_signal and mode in {"B", "A+B", "C+B"}:
        confirm_event[trigger_t] = 1
        local_median[trigger_t] = 0.10
        local_median_available[trigger_t] = True

    return ZigZagPerBar(
        candidate_height_pct=candidate_height,
        confirm_event=confirm_event,
        confirmed_leg_idx_at_t=np.full(n, -1, dtype=np.int64),
        last_confirmed_leg_height_pct=np.full(n, np.nan, dtype=np.float64),
        local_median_N=local_median,
        local_median_available=local_median_available,
        candidate_age_bars=candidate_age,
        candidate_leg_direction=candidate_direction,
    )


def _volume_runtime(n: int, blocked_bars: set[int] | None = None) -> VolumeRuntime:
    blocked_bars = blocked_bars or set()
    allowed = np.ones(n, dtype=bool)
    block_reason = np.full(n, BLOCK_NONE, dtype=np.int8)
    for t in blocked_bars:
        allowed[t] = False
        block_reason[t] = BLOCK_BELOW_BASELINE

    return VolumeRuntime(
        short_median_volume=np.ones(n, dtype=np.float64),
        baseline_median_volume=np.ones(n, dtype=np.float64),
        median_relative_volume=np.ones(n, dtype=np.float64),
        volume_regime=np.full(n, REGIME_NORMAL, dtype=np.int8),
        volume_condition_allowed=allowed,
        volume_condition_block_reason=block_reason,
        volume_initial_direction=np.full(n, DIR_LONG, dtype=np.int8),
        absolute_offset=0,
        reference_length=n,
        filter_config_snapshot={"volume_filter_enabled": True},
    )


def _run(
    *,
    mode: str = "A",
    n: int = 5,
    trigger_t: int = 1,
    blocked_bars: set[int] | None = None,
    zigzag_signal: bool = True,
    daily_reset_event: np.ndarray | None = None,
    time_filter_events: tuple[np.ndarray, np.ndarray] | None = None,
):
    return apply(
        trend=np.array([1, 1, -1, -1, 1, -1, 1, -1][:n], dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_TradeFilter(),
        zigzag_global_stats=_stats(mode),
        per_bar=_per_bar(
            mode=mode,
            n=n,
            trigger_t=trigger_t,
            zigzag_signal=zigzag_signal,
        ),
        daily_reset_event=daily_reset_event,
        time_filter_events=time_filter_events,
        volume_runtime=_volume_runtime(n, blocked_bars),
    )


def test_and_logic_zigzag_allowed_volume_blocked_stays_off():
    result = _run(mode="A", blocked_bars={1})
    diag = result.filter_diagnostics

    assert diag["trade_filter_state"][1] == "OFF"
    assert diag["trade_filter_trigger_source"][1] == "none"
    assert diag["filter_block_reason"][1] == "volume_below_baseline"


def test_and_logic_zigzag_blocked_volume_allowed_stays_off():
    result = _run(mode="A", zigzag_signal=False)
    diag = result.filter_diagnostics

    assert diag["trade_filter_state"][1] == "OFF"
    assert diag["trade_filter_trigger_source"][1] == "none"
    assert diag["filter_block_reason"][1] == "none"


def test_and_logic_both_allowed_starts_lifecycle():
    result = _run(mode="A")
    diag = result.filter_diagnostics

    assert diag["trade_filter_state"][1] == "WAIT_FIRST_ST_FLIP"
    assert diag["trade_filter_trigger_source"][1] == "candidate_threshold"


@pytest.mark.parametrize("mode", ["A", "B", "C", "A+B", "C+B"])
def test_volume_blocks_lifecycle_start_in_each_mode(mode):
    result = _run(mode=mode, blocked_bars={1})
    diag = result.filter_diagnostics

    assert diag["trade_filter_state"][1] == "OFF"
    assert diag["trade_filter_trigger_source"][1] == "none"
    assert diag["filter_block_reason"][1] == "volume_below_baseline"


def test_volume_blocks_start_after_daily_reset_returns_to_off():
    n = 6
    daily_reset = np.array([0, 0, 0, 1, 0, 0], dtype=np.int8)
    result = _run(
        mode="A",
        n=n,
        trigger_t=4,
        blocked_bars={4},
        daily_reset_event=daily_reset,
    )
    diag = result.filter_diagnostics

    assert diag["trade_filter_state"][3] == "OFF"
    assert diag["trade_filter_state"][4] == "OFF"
    assert diag["filter_block_reason"][4] == "volume_below_baseline"


def test_volume_blocks_start_after_time_filter_reset_returns_to_off():
    n = 6
    in_window = np.ones(n, dtype=bool)
    time_reset = np.array([0, 0, 0, 1, 0, 0], dtype=bool)
    result = _run(
        mode="A",
        n=n,
        trigger_t=4,
        blocked_bars={4},
        time_filter_events=(in_window, time_reset),
    )
    diag = result.filter_diagnostics

    assert diag["trade_filter_state"][3] == "OFF"
    assert diag["trade_filter_state"][4] == "OFF"
    assert diag["filter_block_reason"][4] == "volume_below_baseline"


def test_volume_does_not_gate_wait_first_st_flip_to_active():
    result = _run(mode="A", blocked_bars={2})
    diag = result.filter_diagnostics

    assert diag["trade_filter_state"][1] == "WAIT_FIRST_ST_FLIP"
    assert diag["volume_condition_allowed"][2] == np.False_
    assert diag["trade_filter_state"][2] in {
        "ST_ACTIVE_FREEZE",
        "ST_ACTIVE_MONITORING",
    }


def test_volume_categorical_diagnostics_are_object_strings():
    result = _run(mode="A", blocked_bars={1})
    diag = result.filter_diagnostics

    for key in (
        "volume_regime",
        "volume_condition_block_reason",
        "volume_initial_direction",
    ):
        assert diag[key].dtype == object
        assert isinstance(diag[key][1], str)
    assert diag["volume_condition_block_reason"][1] == "volume_below_baseline"


@pytest.mark.parametrize(
    "case_id",
    [
        "f1_mode_a_candidate",
        "f2_mode_b_confirmed",
        "f3_mode_c_immediate",
        "f4_mode_ab_both",
        "f5_mode_cb_rescue",
        "f6_exit_b_counting",
        "f7_daily_reset",
        "f8_time_filter_reset",
    ],
)
def test_volume_runtime_none_keeps_w4_1_golden_snapshots(case_id):
    from wf_grid.tests.test_zigzag_apply_characterization import (
        _load_snapshot,
        _run_case,
    )

    assert _run_case(case_id) == _load_snapshot(case_id)
