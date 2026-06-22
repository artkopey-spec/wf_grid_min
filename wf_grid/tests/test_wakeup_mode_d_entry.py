from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.core.zigzag_st_filter import (
    ZigZagFSMState,
    ZigZagGlobalStats,
    ZigZagPerBar,
    _compute_wakeup_atr_ratio,
    _effective_wakeup_trade_mode,
    apply_wakeup_direction_mode,
    attach_trade_filter_diagnostics as attach_trade_filter_diagnostics_public,
    apply,
)
import supertrend_optimizer.core.zigzag_st_filter as zigzag_st_filter_module
from supertrend_optimizer.core.backtest import calculate_returns
from supertrend_optimizer.core.filter_trade_diagnostics import (
    attach_trade_filter_diagnostics,
)
from supertrend_optimizer.core.trades import extract_trades, net_pnl_pct_to_micropct
from supertrend_optimizer.engine.result import BacktestResult
from supertrend_optimizer.io.excel_tester import (
    FILTER_DIAGNOSTICS_100_DISPLAY_NAMES,
    _build_filters_summary_df,
    _build_zigzag_trigger_events_df,
    export_tester_results,
)
from supertrend_optimizer.testing.runner import (
    PeriodResult,
    _build_filter_diagnostics_summary,
)
from supertrend_optimizer.testing.signal_events import build_signal_events
from supertrend_optimizer.utils.enums import ExecutionModel
from supertrend_optimizer.utils.exceptions import ConfigError


MODE_D_WAKEUP_EXPECTED_KEYS = frozenset({
    "wakeup_regime_active",
    "wakeup_entry_all_ok",
    "wakeup_entry_candidate_height_ok",
    "wakeup_entry_candidate_age_ok",
    "wakeup_entry_candidate_direction_ok",
    "wakeup_entry_trade_mode_ok",
    "wakeup_entry_atr_ok",
    "wakeup_entry_volume_ok",
    "wakeup_entry_candidate_height_value",
    "wakeup_entry_candidate_height_threshold",
    "wakeup_entry_candidate_age_bars",
    "wakeup_entry_candidate_leg_direction",
    "wakeup_entry_atr_ratio",
    "wakeup_entry_volume_ratio",
    "wakeup_cycle_age_bars",
    "wakeup_bars_since_fresh_candidate",
    "wakeup_cycle_trade_count",
    "wakeup_cycle_realized_pnl_pct",
    "wakeup_exit_cycle_take_profit_triggered",
    "wakeup_exit_ttl_triggered",
    "wakeup_exit_no_fresh_candidate_triggered",
    "wakeup_exit_local_median_stop_triggered",
    "wakeup_exit_cycle_trade_limit_triggered",
    "wakeup_exit_close_triggered",
    "wakeup_exit_action_mode",
    "wakeup_exit_reason",
    "wakeup_position_action",
    "wakeup_active_direction",
    "wakeup_lock_cycle_direction_config",
    "wakeup_cycle_trade_limit_config",
})

MODE_D_POSITION_FREEZE_EXPECTED_KEYS = frozenset({
    "position_freeze_active",
    "position_freeze_bars_left",
    "position_freeze_ignored_opposite_st_flip",
    "position_freeze_release_action",
})


def _cfg(
    *,
    height_enabled: bool = True,
    age_enabled: bool = True,
    atr_enabled: bool = False,
    volume_enabled: bool = False,
    ttl_enabled: bool = True,
    ttl_bars: int = 10,
    no_fresh_enabled: bool = False,
    no_fresh_timeout_bars: int = 3,
    local_median_stop_enabled: bool = False,
    max_trades_enabled: bool = False,
    max_trades: int | None = None,
    cycle_take_profit_enabled: bool = False,
    cycle_take_profit_pnl_pct: float | None = None,
    action_mode: str = "block_new_entries",
    lock_cycle_direction: bool = False,
    position_freeze_enabled: bool = False,
    position_freeze_min_hold_bars: int = 2,
    direction_mode: object = "normal",
) -> SimpleNamespace:
    return SimpleNamespace(
        lifecycle=SimpleNamespace(
            freeze_confirmed_legs=0,
            exit_off_mode="exit C",
            exit_b_immediate_off=False,
        ),
        wakeup_regime=SimpleNamespace(
            enabled=True,
            lock_cycle_direction=lock_cycle_direction,
            entry=SimpleNamespace(
                candidate_height=SimpleNamespace(
                    enabled=height_enabled,
                    quantile=0.65,
                ),
                candidate_age=SimpleNamespace(
                    enabled=age_enabled,
                    max_bars=4,
                ),
                atr_expansion=SimpleNamespace(
                    enabled=atr_enabled,
                    short_window=2,
                    long_window=4,
                    min_ratio=1.0,
                ),
                volume_expansion=SimpleNamespace(
                    enabled=volume_enabled,
                    short_window=2,
                    baseline_window=3,
                    min_ratio=1.0,
                ),
                direction_mode=direction_mode,
            ),
            exit=SimpleNamespace(
                ttl=SimpleNamespace(enabled=ttl_enabled, bars=ttl_bars),
                no_fresh_candidate=SimpleNamespace(
                    enabled=no_fresh_enabled,
                    quantile=0.60,
                    max_age_bars=2,
                    timeout_bars=no_fresh_timeout_bars,
                ),
                max_trades_per_cycle=SimpleNamespace(
                    enabled=max_trades_enabled,
                    max_trades=max_trades,
                ),
                cycle_take_profit=SimpleNamespace(
                    enabled=cycle_take_profit_enabled,
                    pnl_pct=cycle_take_profit_pnl_pct,
                ),
                local_median_stop=SimpleNamespace(
                    enabled=local_median_stop_enabled,
                ),
                action=SimpleNamespace(mode=action_mode),
            ),
            position_freeze=SimpleNamespace(
                enabled=position_freeze_enabled,
                min_hold_bars=position_freeze_min_hold_bars,
                apply_to="internal_opposite_st_flip",
                release_action="apply_if_still_opposite",
            ),
        ),
    )


def _stats(*, no_fresh_threshold: float | None = None) -> ZigZagGlobalStats:
    return ZigZagGlobalStats(
        reversal_threshold=0.01,
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
        metadata={},
        zigzag_mode="D",
        wakeup_entry_candidate_height_threshold=0.10,
        wakeup_no_fresh_candidate_height_threshold=no_fresh_threshold,
    )


def _stats_for_mode(zigzag_mode: str) -> ZigZagGlobalStats:
    return ZigZagGlobalStats(
        reversal_threshold=0.01,
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
        metadata={},
        zigzag_mode=zigzag_mode,
    )



def _per_bar(
    *,
    height: float = 0.12,
    age: int = 3,
    direction: int = 1,
    t: int = 1,
    n: int = 6,
) -> ZigZagPerBar:
    candidate_height = np.full(n, np.nan, dtype=np.float64)
    candidate_height[t] = height
    candidate_age = np.full(n, -1, dtype=np.int64)
    candidate_age[t] = age
    candidate_direction = np.zeros(n, dtype=np.int8)
    candidate_direction[t] = direction
    return ZigZagPerBar(
        candidate_height_pct=candidate_height,
        confirm_event=np.zeros(n, dtype=np.int8),
        confirmed_leg_idx_at_t=np.full(n, -1, dtype=np.int64),
        last_confirmed_leg_height_pct=np.full(n, np.nan, dtype=np.float64),
        local_median_N=np.full(n, np.nan, dtype=np.float64),
        local_median_available=np.zeros(n, dtype=bool),
        candidate_age_bars=candidate_age,
        candidate_leg_direction=candidate_direction,
    )


def _per_bar_with_lms(
    *,
    lms_bar: int = 2,
    local_median: float = 0.01,
    available: bool = True,
    confirmed: bool = True,
    n: int = 6,
) -> ZigZagPerBar:
    per_bar = _per_bar(t=1, n=n)
    per_bar.confirm_event[lms_bar] = np.int8(1 if confirmed else 0)
    per_bar.local_median_N[lms_bar] = local_median
    per_bar.local_median_available[lms_bar] = available
    return per_bar


def _valid_ohlc(n: int = 6) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    close = np.arange(10.0, 10.0 + n, dtype=np.float64)
    high = close + 1.0
    low = close - 1.0
    return high, low, close


def test_apply_wakeup_direction_mode_inverse_returns_new_inverted_array():
    inp = np.array([0, 1, 1, -1, -1, 0], dtype=np.int8)
    before = inp.copy()

    result = apply_wakeup_direction_mode(
        inp,
        _cfg(direction_mode="inverse"),
        mode_d_enabled=True,
    )

    np.testing.assert_array_equal(
        result,
        np.array([0, -1, -1, 1, 1, 0], dtype=np.int8),
    )
    assert result.dtype == np.int8
    assert result[0] == 0
    assert result[-1] == 0
    np.testing.assert_array_equal(inp, before)
    assert result is not inp


@pytest.mark.parametrize(
    "config, mode_d_enabled",
    [
        (_cfg(direction_mode="normal"), True),
        (_cfg(direction_mode="inverse"), False),
        (_cfg(direction_mode="inverse"), np.bool_(True)),
        (_cfg(direction_mode="inverse"), 1),
        (_cfg(direction_mode="inverse"), None),
        (_cfg(direction_mode="inverse"), False),
        (_cfg(direction_mode="normal"), False),
        (None, True),
        (SimpleNamespace(), True),
        (SimpleNamespace(wakeup_regime=SimpleNamespace(enabled=False)), True),
        (SimpleNamespace(wakeup_regime=SimpleNamespace(enabled=True)), True),
        (
            SimpleNamespace(
                wakeup_regime=SimpleNamespace(
                    enabled=True,
                    entry=SimpleNamespace(),
                )
            ),
            True,
        ),
    ],
)
def test_apply_wakeup_direction_mode_noop_returns_same_object(config, mode_d_enabled):
    inp = np.array([0, 1, 1, -1, -1, 0], dtype=np.int8)

    result = apply_wakeup_direction_mode(inp, config, mode_d_enabled=mode_d_enabled)

    assert result is inp
    np.testing.assert_array_equal(inp, np.array([0, 1, 1, -1, -1, 0], dtype=np.int8))


def test_mode_d_counters_trigger_bar_fresh_candidate_starts_at_zero():
    result = apply(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(no_fresh_enabled=True),
        zigzag_global_stats=_stats(no_fresh_threshold=0.10),
        per_bar=_per_bar(t=1, age=2),
    )

    diag = result.filter_diagnostics
    assert int(diag["wakeup_cycle_age_bars"][1]) == 0
    assert int(diag["wakeup_bars_since_fresh_candidate"][1]) == 0
    assert int(diag["wakeup_regime_active"][1]) == 1


def test_mode_d_collect_filter_diagnostics_false_returns_none_and_same_positions():
    kwargs = dict(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(no_fresh_enabled=True),
        zigzag_global_stats=_stats(no_fresh_threshold=0.10),
        per_bar=_per_bar(t=1, age=2),
    )

    enabled = apply(**kwargs, collect_filter_diagnostics=True)
    disabled = apply(**kwargs, collect_filter_diagnostics=False)

    assert enabled.filter_diagnostics is not None
    assert disabled.filter_diagnostics is None
    np.testing.assert_array_equal(enabled.positions, disabled.positions)


def test_mode_d_direction_mode_absent_equals_explicit_normal():
    kwargs = dict(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="both",
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1),
    )

    absent = apply(trade_filter_config=_cfg(), **kwargs)
    explicit = apply(trade_filter_config=_cfg(direction_mode="normal"), **kwargs)

    np.testing.assert_array_equal(absent.positions, explicit.positions)
    assert absent.filter_diagnostics.keys() == explicit.filter_diagnostics.keys()


def test_mode_d_direction_mode_inverse_applies_to_both_diagnostic_branches():
    kwargs = dict(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(direction_mode="inverse"),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1),
    )

    enabled = apply(**kwargs, collect_filter_diagnostics=True)
    disabled = apply(**kwargs, collect_filter_diagnostics=False)

    np.testing.assert_array_equal(enabled.positions, [0, 0, -1, -1, -1, -1])
    np.testing.assert_array_equal(enabled.positions, disabled.positions)
    assert enabled.filter_diagnostics is not None
    assert disabled.filter_diagnostics is None


def test_mode_d_direction_mode_inverse_keeps_diagnostics_pre_inversion():
    kwargs = dict(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="both",
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1),
    )

    normal = apply(trade_filter_config=_cfg(direction_mode="normal"), **kwargs)
    inverse = apply(trade_filter_config=_cfg(direction_mode="inverse"), **kwargs)

    assert normal.filter_diagnostics.keys() == inverse.filter_diagnostics.keys()
    np.testing.assert_array_equal(inverse.positions, -normal.positions)
    for key in (
        "wakeup_active_direction",
        "wakeup_entry_candidate_leg_direction",
        "trade_filter_trigger_source",
        "wakeup_position_action",
    ):
        np.testing.assert_array_equal(
            inverse.filter_diagnostics[key],
            normal.filter_diagnostics[key],
        )


def test_mode_d_direction_mode_inverse_mirrors_positions_and_trades():
    kwargs = dict(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="both",
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1),
    )

    normal = apply(trade_filter_config=_cfg(direction_mode="normal"), **kwargs)
    inverse = apply(trade_filter_config=_cfg(direction_mode="inverse"), **kwargs)
    open_prices = np.array([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])
    index = pd.RangeIndex(len(open_prices))

    returns_normal = calculate_returns(
        open_prices,
        normal.positions,
        0.0,
        execution_model=ExecutionModel.OPEN_TO_OPEN,
    )
    returns_inverse = calculate_returns(
        open_prices,
        inverse.positions,
        0.0,
        execution_model=ExecutionModel.OPEN_TO_OPEN,
    )
    trades_normal = extract_trades(
        normal.positions,
        returns_normal,
        open_prices,
        index,
        0.0,
        trend=np.zeros(6, dtype=np.int64),
    )
    trades_inverse = extract_trades(
        inverse.positions,
        returns_inverse,
        open_prices,
        index,
        0.0,
        trend=np.zeros(6, dtype=np.int64),
    )

    assert np.count_nonzero(normal.positions) >= 1
    assert len(trades_normal) >= 1
    np.testing.assert_array_equal(inverse.positions, -normal.positions)
    assert np.abs(inverse.positions).sum() == np.abs(normal.positions).sum()
    assert len(trades_inverse) == len(trades_normal)
    np.testing.assert_array_equal(
        trades_inverse["entry_index"].to_numpy(),
        trades_normal["entry_index"].to_numpy(),
    )
    np.testing.assert_array_equal(
        trades_inverse["exit_index"].to_numpy(),
        trades_normal["exit_index"].to_numpy(),
    )
    assert trades_normal["direction"].tolist() == ["LONG"]
    assert trades_inverse["direction"].tolist() == ["SHORT"]
    np.testing.assert_allclose(
        trades_inverse["gross_pnl_pct"].to_numpy(),
        -trades_normal["gross_pnl_pct"].to_numpy(),
        atol=1e-6,
    )
    np.testing.assert_array_equal(
        inverse.filter_diagnostics["wakeup_active_direction"],
        normal.filter_diagnostics["wakeup_active_direction"],
    )


@pytest.mark.parametrize(
    ("action_mode", "expected_next_pos"),
    [
        ("block_new_entries", 1),
        ("close_position", 0),
    ],
)
def test_mode_d_local_median_stop_fires_on_confirmed_leg_bar(
    action_mode,
    expected_next_pos,
):
    result = apply(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            local_median_stop_enabled=True,
            action_mode=action_mode,
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar_with_lms(lms_bar=2),
    )

    diag = result.filter_diagnostics
    assert int(diag["wakeup_exit_local_median_stop_triggered"][2]) == 1
    assert diag["wakeup_exit_reason"][2] == "local_median_stop"
    assert diag["wakeup_position_action"][2] == "exit_local_median_stop"
    assert int(result.positions[3]) == expected_next_pos
    if action_mode == "block_new_entries":
        assert diag["trade_filter_state"][2] == "ST_STOPPING"
    else:
        assert int(diag["wakeup_exit_close_triggered"][2]) == 1


@pytest.mark.parametrize(
    ("per_bar", "expected_reason"),
    [
        (_per_bar_with_lms(local_median=np.nan), "none"),
        (_per_bar_with_lms(available=False), "none"),
        (_per_bar_with_lms(confirmed=False), "none"),
        (_per_bar_with_lms(lms_bar=1), "none"),
    ],
)
def test_mode_d_local_median_stop_fail_open_cases(per_bar, expected_reason):
    result = apply(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            local_median_stop_enabled=True,
        ),
        zigzag_global_stats=_stats(),
        per_bar=per_bar,
    )

    diag = result.filter_diagnostics
    assert int(np.sum(diag["wakeup_exit_local_median_stop_triggered"])) == 0
    assert diag["wakeup_exit_reason"][2] == expected_reason


def test_mode_d_local_median_stop_does_not_fire_on_reset_bar():
    result = apply(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            local_median_stop_enabled=True,
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar_with_lms(lms_bar=2),
        daily_reset_event=np.array([False, False, True, False, False, False]),
    )

    diag = result.filter_diagnostics
    assert int(diag["wakeup_exit_local_median_stop_triggered"][2]) == 0
    assert diag["wakeup_exit_reason"][2] == "reset"


def test_mode_d_local_median_stop_does_not_fire_on_equal_global_median():
    result = apply(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            local_median_stop_enabled=True,
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar_with_lms(lms_bar=2, local_median=0.05),
    )

    diag = result.filter_diagnostics
    assert int(diag["wakeup_exit_local_median_stop_triggered"][2]) == 0
    assert diag["wakeup_exit_reason"][2] == "none"


def test_mode_d_local_median_stop_priority_between_no_fresh_and_cycle_limit():
    ttl_result = apply(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_bars=1,
            local_median_stop_enabled=True,
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar_with_lms(lms_bar=2),
    )
    assert ttl_result.filter_diagnostics["wakeup_exit_reason"][2] == "ttl"
    assert int(
        ttl_result.filter_diagnostics[
            "wakeup_exit_local_median_stop_triggered"
        ][2]
    ) == 0

    no_fresh_result = apply(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            no_fresh_enabled=True,
            no_fresh_timeout_bars=2,
            local_median_stop_enabled=True,
        ),
        zigzag_global_stats=_stats(no_fresh_threshold=0.20),
        per_bar=_per_bar_with_lms(lms_bar=2),
    )
    assert (
        no_fresh_result.filter_diagnostics["wakeup_exit_reason"][2]
        == "no_fresh_candidate"
    )
    assert int(
        no_fresh_result.filter_diagnostics[
            "wakeup_exit_local_median_stop_triggered"
        ][2]
    ) == 0

    no_fresh_not_satisfied_result = apply(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            no_fresh_enabled=True,
            no_fresh_timeout_bars=5,
            local_median_stop_enabled=True,
        ),
        zigzag_global_stats=_stats(no_fresh_threshold=0.20),
        per_bar=_per_bar_with_lms(lms_bar=2),
    )
    assert (
        no_fresh_not_satisfied_result.filter_diagnostics[
            "wakeup_bars_since_fresh_candidate"
        ][2]
        == 2
    )
    assert no_fresh_not_satisfied_result.filter_diagnostics["wakeup_exit_reason"][
        2
    ] == "local_median_stop"
    assert int(
        no_fresh_not_satisfied_result.filter_diagnostics[
            "wakeup_exit_local_median_stop_triggered"
        ][2]
    ) == 1
    assert int(
        no_fresh_not_satisfied_result.filter_diagnostics[
            "wakeup_exit_no_fresh_candidate_triggered"
        ][2]
    ) == 0

    cycle_result = apply(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            local_median_stop_enabled=True,
            max_trades_enabled=True,
            max_trades=1,
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar_with_lms(lms_bar=2),
    )
    assert cycle_result.filter_diagnostics["wakeup_exit_reason"][2] == (
        "local_median_stop"
    )
    assert int(
        cycle_result.filter_diagnostics[
            "wakeup_exit_cycle_trade_limit_triggered"
        ][2]
    ) == 0


def test_mode_d_local_median_stop_suppresses_same_bar_internal_st_flip():
    result = apply(
        trend=np.array([1, 1, -1, -1, -1, -1], dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            local_median_stop_enabled=True,
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar_with_lms(lms_bar=2),
    )

    diag = result.filter_diagnostics
    assert diag["wakeup_exit_reason"][2] == "local_median_stop"
    assert diag["wakeup_position_action"][2] == "exit_local_median_stop"


def test_mode_d_local_median_stop_lite_matches_diagnostics_and_skips_arrays(
    monkeypatch,
):
    kwargs = dict(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            local_median_stop_enabled=True,
            action_mode="close_position",
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar_with_lms(lms_bar=2),
    )
    diagnostic = apply(**kwargs, collect_filter_diagnostics=True)

    def fail_allocate(*args, **kwargs):
        raise AssertionError("_allocate_apply_arrays called in lite mode")

    monkeypatch.setattr(
        zigzag_st_filter_module,
        "_allocate_apply_arrays",
        fail_allocate,
    )

    lite = apply(**kwargs, collect_filter_diagnostics=False)

    assert diagnostic.filter_diagnostics is not None
    assert lite.filter_diagnostics is None
    np.testing.assert_array_equal(diagnostic.positions, lite.positions)


@pytest.mark.parametrize("max_trades", [1, 3])
@pytest.mark.parametrize("action_mode", ["block_new_entries", "close_position"])
@pytest.mark.parametrize("lock_cycle_direction", [False, True])
def test_mode_d_cycle_trade_limit_lite_matches_diagnostics_and_skips_arrays(
    monkeypatch,
    max_trades,
    action_mode,
    lock_cycle_direction,
):
    kwargs = dict(
        trend=np.array([1, 1, 1, -1, 1, -1, 1, 1, 1], dtype=np.int64),
        trade_mode="long",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            max_trades_enabled=True,
            max_trades=max_trades,
            action_mode=action_mode,
            lock_cycle_direction=lock_cycle_direction,
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=9),
    )
    diagnostic = apply(**kwargs, collect_filter_diagnostics=True)

    def fail_allocate(*args, **kwargs):
        raise AssertionError("_allocate_apply_arrays called in lite mode")

    monkeypatch.setattr(
        zigzag_st_filter_module,
        "_allocate_apply_arrays",
        fail_allocate,
    )

    lite = apply(**kwargs, collect_filter_diagnostics=False)

    assert lite.filter_diagnostics is None
    np.testing.assert_array_equal(diagnostic.positions, lite.positions)


def test_mode_d_absent_and_disabled_max_trades_preserve_positions():
    absent_cfg = _cfg()
    delattr(absent_cfg.wakeup_regime.exit, "max_trades_per_cycle")
    disabled_cfg = _cfg(max_trades_enabled=False, max_trades=3)
    kwargs = dict(
        trend=np.array([0, 1, 1, -1, -1, 1], dtype=np.int64),
        trade_mode="both",
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1),
    )

    absent = apply(**kwargs, trade_filter_config=absent_cfg)
    disabled = apply(**kwargs, trade_filter_config=disabled_cfg)

    np.testing.assert_array_equal(absent.positions, disabled.positions)


def test_wakeup_disabled_max_trades_enabled_is_noop_outside_mode_d():
    baseline_cfg = _cfg(max_trades_enabled=False)
    baseline_cfg.lifecycle.exit_off_mode = "exit A"
    disabled_wakeup_cfg = _cfg(max_trades_enabled=True, max_trades=1)
    disabled_wakeup_cfg.lifecycle.exit_off_mode = "exit A"
    disabled_wakeup_cfg.wakeup_regime.enabled = False
    kwargs = dict(
        trend=np.array([0, 1, 1, -1, -1, 1], dtype=np.int64),
        trade_mode="both",
        zigzag_global_stats=_stats_for_mode("A"),
        per_bar=_per_bar(t=1),
    )

    baseline = apply(**kwargs, trade_filter_config=baseline_cfg)
    disabled_wakeup = apply(**kwargs, trade_filter_config=disabled_wakeup_cfg)

    np.testing.assert_array_equal(baseline.positions, disabled_wakeup.positions)


def test_mode_d_counters_trigger_bar_not_fresh_starts_at_one_then_increments():
    result = apply(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(no_fresh_enabled=True),
        zigzag_global_stats=_stats(no_fresh_threshold=0.20),
        per_bar=_per_bar(t=1),
    )

    diag = result.filter_diagnostics
    assert int(diag["wakeup_cycle_age_bars"][1]) == 0
    assert int(diag["wakeup_bars_since_fresh_candidate"][1]) == 1
    assert int(diag["wakeup_cycle_age_bars"][2]) == 1
    assert int(diag["wakeup_bars_since_fresh_candidate"][2]) == 2


def test_mode_d_ttl_exit_condition_fires_once_when_age_reaches_ttl():
    result = apply(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(ttl_bars=2),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1),
    )

    diag = result.filter_diagnostics
    assert diag["wakeup_exit_ttl_triggered"].tolist() == [0, 0, 0, 1, 0, 0]
    assert diag["wakeup_exit_reason"][3] == "ttl"
    assert set(diag["wakeup_exit_reason"]) == {"none", "ttl"}


def test_mode_d_no_fresh_exit_condition_fires_when_timeout_reached():
    result = apply(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            no_fresh_enabled=True,
            no_fresh_timeout_bars=2,
        ),
        zigzag_global_stats=_stats(no_fresh_threshold=0.20),
        per_bar=_per_bar(t=1),
    )

    diag = result.filter_diagnostics
    assert diag["wakeup_exit_no_fresh_candidate_triggered"].tolist() == [
        0, 0, 1, 0, 0, 0
    ]
    assert diag["wakeup_exit_reason"][2] == "no_fresh_candidate"


def test_mode_d_exit_c_condition_priority_ttl_over_no_fresh():
    result = apply(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_bars=1,
            no_fresh_enabled=True,
            no_fresh_timeout_bars=2,
        ),
        zigzag_global_stats=_stats(no_fresh_threshold=0.20),
        per_bar=_per_bar(t=1),
    )

    diag = result.filter_diagnostics
    assert int(diag["wakeup_exit_ttl_triggered"][2]) == 1
    assert int(diag["wakeup_exit_no_fresh_candidate_triggered"][2]) == 0
    assert diag["wakeup_exit_reason"][2] == "ttl"


def test_mode_d_block_new_entries_holds_until_opposite_st_flip():
    result = apply(
        trend=np.array([0, 0, 1, 1, -1, -1], dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(ttl_bars=1),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1),
    )

    diag = result.filter_diagnostics
    assert result.positions.tolist() == [0, 0, 1, 1, 1, 0]
    assert int(diag["wakeup_exit_ttl_triggered"][2]) == 1
    assert int(diag["wakeup_exit_close_triggered"][2]) == 0
    assert diag["wakeup_exit_reason"][2] == "ttl"
    assert diag["trade_filter_state"][2] == "ST_STOPPING"
    assert diag["wakeup_exit_reason"][4] == "none"
    assert diag["wakeup_position_action"][4] == "none"
    assert int(diag["wakeup_regime_active"][4]) == 0
    assert int(diag["wakeup_active_direction"][4]) == 0


def test_mode_d_lock_stopping_does_not_apply_locked_st_flip_handling():
    result = apply(
        trend=np.array([0, 0, 1, 1, -1, -1], dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(ttl_bars=1, lock_cycle_direction=True),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1),
    )

    diag = result.filter_diagnostics
    assert result.positions.tolist() == [0, 0, 1, 1, 1, 0]
    assert diag["trade_filter_state"][2] == "ST_STOPPING"
    assert diag["wakeup_position_action"][4] == "none"
    assert diag["wakeup_exit_reason"][4] == "none"
    assert diag["trade_filter_state"][4] == "OFF"


def test_mode_d_close_position_action_closes_on_exit_c_bar():
    result = apply(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(ttl_bars=1, action_mode="close_position"),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1),
    )

    diag = result.filter_diagnostics
    assert result.positions.tolist() == [0, 0, 1, 0, 0, 0]
    assert int(diag["wakeup_exit_ttl_triggered"][2]) == 1
    assert int(diag["wakeup_exit_close_triggered"][2]) == 1
    assert diag["wakeup_exit_reason"][2] == "ttl"
    assert diag["trade_filter_state"][2] == "OFF"
    assert "ST_STOPPING" not in set(diag["trade_filter_state"])


@pytest.mark.parametrize(
    ("exit_reason", "cfg_kwargs", "stats_kwargs"),
    [
        ("ttl", {"ttl_bars": 1}, {}),
        (
            "no_fresh_candidate",
            {
                "ttl_enabled": False,
                "no_fresh_enabled": True,
                "no_fresh_timeout_bars": 2,
            },
            {"no_fresh_threshold": 0.20},
        ),
    ],
)
@pytest.mark.parametrize(
    ("action_mode", "expected_positions"),
    [
        ("block_new_entries", [0, 0, 1, 1, 1, 0]),
        ("close_position", [0, 0, 1, 0, 0, 0]),
    ],
)
@pytest.mark.parametrize("lock_cycle_direction", [False, True])
def test_mode_d_exit_c_positions_golden_before_refactor(
    exit_reason,
    cfg_kwargs,
    stats_kwargs,
    action_mode,
    expected_positions,
    lock_cycle_direction,
):
    result = apply(
        trend=np.array([0, 0, 1, 1, -1, -1], dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            **cfg_kwargs,
            action_mode=action_mode,
            lock_cycle_direction=lock_cycle_direction,
        ),
        zigzag_global_stats=_stats(**stats_kwargs),
        per_bar=_per_bar(t=1),
    )

    assert result.positions.tolist() == expected_positions
    assert exit_reason in set(result.filter_diagnostics["wakeup_exit_reason"])


def test_mode_d_cycle_trade_limit_max_one_close_position_next_active_bar():
    result = apply(
        trend=np.zeros(5, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            max_trades_enabled=True,
            max_trades=1,
            action_mode="close_position",
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=5),
    )

    diag = result.filter_diagnostics
    assert result.positions.tolist() == [0, 0, 1, 0, 0]
    assert diag["wakeup_exit_reason"][2] == "cycle_trade_limit"
    assert diag["wakeup_position_action"][2] == "exit_cycle_trade_limit"
    assert diag["wakeup_cycle_trade_count"].tolist() == [-1, 1, -1, -1, -1]
    assert int(diag["wakeup_exit_cycle_trade_limit_triggered"][2]) == 1
    assert set(diag["wakeup_cycle_trade_limit_config"]) == {1}
    assert int(diag["wakeup_exit_close_triggered"][2]) == 1
    assert diag["trade_filter_state"][2] == "OFF"


def test_mode_d_cycle_take_profit_threshold_exact_triggers_close_position():
    trend = np.array([0, 1, -1, -1, -1], dtype=np.int64)
    result = apply(
        trend=trend,
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            cycle_take_profit_enabled=True,
            cycle_take_profit_pnl_pct=0.01,
            action_mode="close_position",
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=5),
        open_prices=np.array([100.0, 100.0, 100.0, 101.0, 101.0]),
        commission_rate=0.0,
    )

    diag = result.filter_diagnostics
    assert int(diag["wakeup_exit_cycle_take_profit_triggered"][3]) == 1
    assert int(diag["wakeup_exit_close_triggered"][3]) == 1
    assert diag["wakeup_exit_reason"][3] == "cycle_take_profit"
    assert diag["wakeup_position_action"][3] == "exit_cycle_take_profit"
    assert diag["wakeup_cycle_realized_pnl_pct"][3] == 0.01
    assert int(diag["wakeup_exit_cycle_take_profit_triggered"].sum()) == 1
    assert result.positions.tolist() == [0, 0, 1, -1, 0]


def test_mode_d_cycle_take_profit_below_threshold_does_not_trigger():
    trend = np.array([0, 1, -1, -1, -1], dtype=np.int64)
    result = apply(
        trend=trend,
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            cycle_take_profit_enabled=True,
            cycle_take_profit_pnl_pct=0.01,
            action_mode="close_position",
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=5),
        open_prices=np.array([100.0, 100.0, 100.0, 100.99, 100.99]),
        commission_rate=0.0,
    )

    diag = result.filter_diagnostics
    assert int(diag["wakeup_exit_cycle_take_profit_triggered"].sum()) == 0
    assert diag["wakeup_cycle_realized_pnl_pct"][3] == 0.0099


def test_mode_d_cycle_take_profit_lite_positions_match_diagnostics():
    trend = np.array([0, 1, -1, -1, -1], dtype=np.int64)
    kwargs = dict(
        trend=trend,
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            cycle_take_profit_enabled=True,
            cycle_take_profit_pnl_pct=0.01,
            action_mode="close_position",
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=5),
        open_prices=np.array([100.0, 100.0, 100.0, 101.0, 101.0]),
        commission_rate=0.0,
    )

    diag_result = apply(**kwargs)
    lite_result = apply(**kwargs, collect_filter_diagnostics=False)

    assert lite_result.filter_diagnostics is None
    assert lite_result.positions.tolist() == diag_result.positions.tolist()


def test_mode_d_cycle_take_profit_block_new_entries_does_not_close_immediately():
    result = apply(
        trend=np.array([0, 1, -1, -1, 1, 1], dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            cycle_take_profit_enabled=True,
            cycle_take_profit_pnl_pct=0.01,
            action_mode="block_new_entries",
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=6),
        open_prices=np.array([100.0, 100.0, 100.0, 101.0, 101.0, 101.0]),
        commission_rate=0.0,
    )

    diag = result.filter_diagnostics
    assert int(diag["wakeup_exit_cycle_take_profit_triggered"][3]) == 1
    assert int(diag["wakeup_exit_close_triggered"][3]) == 0
    assert diag["wakeup_exit_reason"][3] == "cycle_take_profit"
    assert diag["trade_filter_state"][3] == "ST_STOPPING"
    assert result.positions.tolist() == [0, 0, 1, -1, -1, 0]


def test_mode_d_cycle_take_profit_final_bar_requests_action_without_new_close():
    result = apply(
        trend=np.array([0, 1, -1, -1], dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            cycle_take_profit_enabled=True,
            cycle_take_profit_pnl_pct=0.01,
            action_mode="close_position",
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=4),
        open_prices=np.array([100.0, 100.0, 100.0, 101.0]),
        commission_rate=0.0,
    )

    diag = result.filter_diagnostics
    assert result.positions.tolist() == [0, 0, 1, -1]
    assert int(diag["wakeup_exit_cycle_take_profit_triggered"][3]) == 1
    assert int(diag["wakeup_exit_close_triggered"][3]) == 1

    trades = extract_trades(
        result.positions,
        returns=np.zeros(3, dtype=np.float64),
        execution_prices=np.array([100.0, 100.0, 100.0, 101.0]),
        index=pd.date_range("2026-01-01", periods=4, freq="min"),
        commission_rate=0.0,
        trend=np.array([0, 1, -1, -1], dtype=np.int64),
    )
    assert trades.iloc[-1]["entry_index"] == 3
    assert trades.iloc[-1]["exit_index"] == 3
    assert trades.iloc[-1]["bars_held"] == 0


def test_mode_d_cycle_take_profit_threshold_uses_net_pnl_after_commission():
    result = apply(
        trend=np.array([0, 1, -1, -1, -1], dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            cycle_take_profit_enabled=True,
            cycle_take_profit_pnl_pct=0.009,
            action_mode="close_position",
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=5),
        open_prices=np.array([100.0, 100.0, 100.0, 101.0, 101.0]),
        commission_rate=0.001,
    )

    diag = result.filter_diagnostics
    assert int(diag["wakeup_exit_cycle_take_profit_triggered"].sum()) == 0
    assert diag["wakeup_cycle_realized_pnl_pct"][3] == pytest.approx(0.008)


def test_mode_d_cycle_take_profit_reset_caused_close_is_excluded():
    result = apply(
        trend=np.array([0, 1, 1, 1, 1], dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            cycle_take_profit_enabled=True,
            cycle_take_profit_pnl_pct=0.01,
            action_mode="close_position",
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=5),
        daily_reset_event=np.array([False, False, True, False, False]),
        open_prices=np.array([100.0, 100.0, 100.0, 200.0, 200.0]),
        commission_rate=0.0,
    )

    diag = result.filter_diagnostics
    assert result.positions.tolist() == [0, 0, 1, 0, 0]
    assert int(diag["wakeup_exit_cycle_take_profit_triggered"].sum()) == 0
    assert diag["wakeup_cycle_realized_pnl_pct"][3] == 0.0


def test_mode_d_cycle_take_profit_membership_matches_trade_net_micropct():
    prices = np.array([100.0, 100.0, 100.0, 101.0, 101.0])
    result = apply(
        trend=np.array([0, 1, -1, -1, -1], dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            cycle_take_profit_enabled=True,
            cycle_take_profit_pnl_pct=0.01,
            action_mode="close_position",
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=5),
        open_prices=prices,
        commission_rate=0.0,
    )

    trades = extract_trades(
        result.positions,
        returns=np.zeros(4, dtype=np.float64),
        execution_prices=prices,
        index=pd.date_range("2026-01-01", periods=5, freq="min"),
        commission_rate=0.0,
        trend=np.array([0, 1, -1, -1, -1], dtype=np.int64),
    )
    realized_micropct = int(
        round(result.filter_diagnostics["wakeup_cycle_realized_pnl_pct"][3] * 1e8)
    )
    member_micropct = net_pnl_pct_to_micropct(trades.iloc[0]["net_pnl_pct"])
    assert realized_micropct == member_micropct


def test_mode_d_cycle_take_profit_beats_ttl_on_same_bar():
    result = apply(
        trend=np.array([0, 1, -1, -1, -1], dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=True,
            ttl_bars=2,
            cycle_take_profit_enabled=True,
            cycle_take_profit_pnl_pct=0.01,
            action_mode="close_position",
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=5),
        open_prices=np.array([100.0, 100.0, 100.0, 101.0, 101.0]),
        commission_rate=0.0,
    )

    diag = result.filter_diagnostics
    assert diag["wakeup_exit_reason"][3] == "cycle_take_profit"
    assert diag["wakeup_position_action"][3] == "exit_cycle_take_profit"
    assert int(diag["wakeup_exit_cycle_take_profit_triggered"][3]) == 1
    assert int(diag["wakeup_exit_ttl_triggered"][3]) == 0


def test_mode_d_cycle_take_profit_one_shot_and_stopping_close_excluded():
    result = apply(
        trend=np.array([0, 1, -1, -1, 1, 1], dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            cycle_take_profit_enabled=True,
            cycle_take_profit_pnl_pct=0.01,
            action_mode="block_new_entries",
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=6),
        open_prices=np.array([100.0, 100.0, 100.0, 101.0, 101.0, 50.0]),
        commission_rate=0.0,
    )

    diag = result.filter_diagnostics
    assert result.positions.tolist() == [0, 0, 1, -1, -1, 0]
    assert diag["wakeup_exit_reason"][3] == "cycle_take_profit"
    assert int(diag["wakeup_exit_cycle_take_profit_triggered"].sum()) == 1
    assert int(diag["wakeup_exit_cycle_take_profit_triggered"][5]) == 0
    assert diag["wakeup_cycle_realized_pnl_pct"][5] == 0.0


def test_mode_d_cycle_take_profit_new_cycle_starts_with_zero_accumulator():
    per_bar = _per_bar(t=1, n=8)
    per_bar.candidate_height_pct[4] = 0.12
    per_bar.candidate_age_bars[4] = 3
    per_bar.candidate_leg_direction[4] = 1

    result = apply(
        trend=np.array([0, 1, -1, -1, 1, 1, -1, -1], dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            cycle_take_profit_enabled=True,
            cycle_take_profit_pnl_pct=0.009,
            action_mode="close_position",
        ),
        zigzag_global_stats=_stats(),
        per_bar=per_bar,
        open_prices=np.array(
            [100.0, 100.0, 100.0, 101.0, 101.0, 101.0, 101.0, 102.0]
        ),
        commission_rate=0.0,
    )

    diag = result.filter_diagnostics
    assert diag["trade_filter_trigger_source"][4] == "wakeup_regime"
    assert diag["wakeup_cycle_realized_pnl_pct"][4] == 0.0
    assert np.flatnonzero(diag["wakeup_exit_cycle_take_profit_triggered"]).tolist() == [
        3,
        7,
    ]


def test_mode_d_cycle_take_profit_time_filter_reset_accumulates_before_reset():
    result = apply(
        trend=np.array([0, 1, -1, -1, -1], dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            cycle_take_profit_enabled=True,
            cycle_take_profit_pnl_pct=0.01,
            action_mode="close_position",
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=5),
        time_filter_events=(
            np.ones(5, dtype=bool),
            np.array([False, False, False, True, False], dtype=bool),
        ),
        open_prices=np.array([100.0, 100.0, 100.0, 101.0, 101.0]),
        commission_rate=0.0,
    )

    diag = result.filter_diagnostics
    assert diag["filter_block_reason"][3] == "time_filter_reset"
    assert diag["wakeup_cycle_realized_pnl_pct"][3] == 0.01
    assert int(diag["wakeup_exit_cycle_take_profit_triggered"].sum()) == 0


def test_mode_d_cycle_take_profit_disabled_preserves_old_positions():
    common = dict(
        trend=np.array([0, 1, -1, -1, -1], dtype=np.int64),
        trade_mode="both",
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=5),
        open_prices=np.array([100.0, 100.0, 100.0, 101.0, 101.0]),
        commission_rate=0.0,
    )
    disabled = apply(
        **common,
        trade_filter_config=_cfg(
            ttl_enabled=False,
            cycle_take_profit_enabled=False,
            action_mode="close_position",
        ),
    )
    absent = apply(
        **common,
        trade_filter_config=_cfg(
            ttl_enabled=False,
            action_mode="close_position",
        ),
    )

    np.testing.assert_array_equal(disabled.positions, absent.positions)
    assert int(disabled.filter_diagnostics["wakeup_exit_cycle_take_profit_triggered"].sum()) == 0


def test_mode_d_cycle_take_profit_membership_matches_reversal_f16_net_micropct():
    prices = np.array([100.0, 100.0, 100.0, 101.0, 101.0])
    result = apply(
        trend=np.array([0, 1, -1, -1, -1], dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            cycle_take_profit_enabled=True,
            cycle_take_profit_pnl_pct=0.008,
            action_mode="close_position",
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=5),
        open_prices=prices,
        commission_rate=0.001,
    )

    trades = extract_trades(
        result.positions,
        returns=np.zeros(4, dtype=np.float64),
        execution_prices=prices,
        index=pd.date_range("2026-01-01", periods=5, freq="min"),
        commission_rate=0.001,
        trend=np.array([0, 1, -1, -1, -1], dtype=np.int64),
    )
    realized_micropct = int(
        round(result.filter_diagnostics["wakeup_cycle_realized_pnl_pct"][3] * 1e8)
    )
    member_micropct = net_pnl_pct_to_micropct(trades.iloc[0]["net_pnl_pct"])
    assert trades.iloc[0]["commission_pct"] == 0.2
    assert realized_micropct == member_micropct


def test_mode_d_cycle_trade_limit_block_new_entries_prevents_fourth_opening():
    result = apply(
        trend=np.array([1, 1, 1, -1, -1, 1, 1, -1, -1], dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            max_trades_enabled=True,
            max_trades=3,
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=9),
    )

    diag = result.filter_diagnostics
    assert result.positions.tolist() == [0, 0, 1, 1, -1, -1, 1, 1, 0]
    assert diag["wakeup_exit_reason"][6] == "cycle_trade_limit"
    assert diag["wakeup_position_action"][6] == "exit_cycle_trade_limit"
    assert int(diag["wakeup_cycle_trade_count"][6]) == 3
    assert int(diag["wakeup_exit_cycle_trade_limit_triggered"][6]) == 1
    assert set(diag["wakeup_cycle_trade_limit_config"]) == {3}
    assert diag["trade_filter_state"][6] == "ST_STOPPING"
    assert diag["wakeup_position_action"][7] == "none"


def test_mode_d_cycle_trade_limit_counts_reverse_on_st_flip():
    result = apply(
        trend=np.array([1, 1, 1, -1, -1, 1], dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            max_trades_enabled=True,
            max_trades=2,
            action_mode="close_position",
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=6),
    )

    diag = result.filter_diagnostics
    assert result.positions.tolist() == [0, 0, 1, 1, -1, 0]
    assert diag["wakeup_position_action"][3] == "reverse_on_st_flip"
    assert int(diag["wakeup_cycle_trade_count"][3]) == 2
    assert diag["wakeup_exit_reason"][4] == "cycle_trade_limit"
    assert diag["wakeup_position_action"][4] == "exit_cycle_trade_limit"


def test_mode_d_cycle_trade_limit_counts_restore_on_st_flip():
    result = apply(
        trend=np.array([1, 1, 1, -1, 1, 1, 1], dtype=np.int64),
        trade_mode="long",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            max_trades_enabled=True,
            max_trades=2,
            action_mode="close_position",
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=7),
    )

    diag = result.filter_diagnostics
    assert result.positions.tolist() == [0, 0, 1, 1, 0, 1, 0]
    assert diag["wakeup_position_action"][3] == "flat_on_disallowed_st_flip"
    assert diag["wakeup_position_action"][4] == (
        "restore_allowed_position_on_st_flip"
    )
    assert int(diag["wakeup_cycle_trade_count"][4]) == 2
    assert diag["wakeup_exit_reason"][5] == "cycle_trade_limit"
    assert diag["wakeup_position_action"][5] == "exit_cycle_trade_limit"


def test_mode_d_cycle_trade_limit_priority_after_ttl_and_no_fresh():
    ttl_result = apply(
        trend=np.zeros(5, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_bars=1,
            max_trades_enabled=True,
            max_trades=1,
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=5),
    )
    no_fresh_result = apply(
        trend=np.zeros(5, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            no_fresh_enabled=True,
            no_fresh_timeout_bars=2,
            max_trades_enabled=True,
            max_trades=1,
        ),
        zigzag_global_stats=_stats(no_fresh_threshold=0.20),
        per_bar=_per_bar(t=1, n=5),
    )

    assert ttl_result.filter_diagnostics["wakeup_exit_reason"][2] == "ttl"
    assert (
        no_fresh_result.filter_diagnostics["wakeup_exit_reason"][2]
        == "no_fresh_candidate"
    )


def test_mode_d_reset_closes_active_cycle_and_writes_reason():
    result = apply(
        trend=np.zeros(5, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(ttl_bars=10),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=5),
        daily_reset_event=np.array([False, False, True, False, False]),
    )

    diag = result.filter_diagnostics
    assert result.positions.tolist() == [0, 0, 1, 0, 0]
    assert diag["wakeup_exit_reason"][2] == "reset"
    assert diag["trade_filter_state"][2] == "OFF"
    assert diag["trade_filter_trigger_source"][2] == "none"
    assert int(diag["wakeup_cycle_age_bars"][2]) == -1


def test_mode_d_reset_allows_new_cycle_limit_to_fire_again():
    per_bar = _per_bar(t=1, n=6)
    per_bar.candidate_height_pct[3] = 0.12
    per_bar.candidate_age_bars[3] = 3
    per_bar.candidate_leg_direction[3] = 1

    result = apply(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            max_trades_enabled=True,
            max_trades=1,
            action_mode="close_position",
        ),
        zigzag_global_stats=_stats(),
        per_bar=per_bar,
        daily_reset_event=np.array([False, False, True, False, False, False]),
    )

    diag = result.filter_diagnostics
    assert result.positions.tolist() == [0, 0, 1, 0, 1, 0]
    assert diag["wakeup_exit_reason"][2] == "reset"
    assert diag["trade_filter_trigger_source"][3] == "wakeup_regime"
    assert diag["wakeup_exit_reason"][4] == "cycle_trade_limit"
    assert diag["wakeup_position_action"][4] == "exit_cycle_trade_limit"


@pytest.mark.parametrize("trade_mode", ["both", "revers"])
def test_mode_d_active_freeze_st_flip_reverses_without_ending_cycle(trade_mode):
    result = apply(
        trend=np.array([1, 1, 1, 1, 1, -1, -1], dtype=np.int64),
        trade_mode=trade_mode,
        trade_filter_config=_cfg(ttl_bars=10),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=7),
    )

    diag = result.filter_diagnostics
    assert result.positions.tolist() == [0, 0, 1, 1, 1, 1, -1]
    assert diag["wakeup_exit_reason"][5] == "none"
    assert diag["wakeup_position_action"][5] == "reverse_on_st_flip"
    assert int(diag["wakeup_active_direction"][5]) == -1
    assert int(diag["wakeup_regime_active"][5]) == 1
    assert diag["trade_filter_state"][5] == "ST_ACTIVE_FREEZE"


def test_mode_d_long_internal_st_flip_flats_then_restores_active_cycle():
    result = apply(
        trend=np.array([1, 1, 1, 1, -1, 1, 1], dtype=np.int64),
        trade_mode="long",
        trade_filter_config=_cfg(ttl_bars=10),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=7),
    )

    diag = result.filter_diagnostics
    assert result.positions.tolist() == [0, 0, 1, 1, 1, 0, 1]
    assert diag["wakeup_exit_reason"][4] == "none"
    assert diag["wakeup_position_action"][4] == "flat_on_disallowed_st_flip"
    assert int(diag["wakeup_active_direction"][4]) == 1
    assert int(diag["wakeup_regime_active"][4]) == 1
    assert diag["wakeup_exit_reason"][5] == "none"
    assert diag["wakeup_position_action"][5] == (
        "restore_allowed_position_on_st_flip"
    )
    assert int(diag["wakeup_active_direction"][5]) == 1
    assert int(diag["wakeup_regime_active"][5]) == 1
    assert diag["trade_filter_state"][5] == "ST_ACTIVE_FREEZE"


def test_mode_d_position_freeze_disabled_exports_default_diagnostics():
    result = apply(
        trend=np.array([1, 1, 1, -1, -1, -1], dtype=np.int64),
        trade_mode="long",
        trade_filter_config=_cfg(ttl_bars=10, position_freeze_enabled=False),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=6),
    )

    diag = result.filter_diagnostics
    assert result.positions.tolist() == [0, 0, 1, 1, 0, 0]
    assert diag["wakeup_position_action"][3] == "flat_on_disallowed_st_flip"
    assert diag["position_freeze_active"].tolist() == [0, 0, 0, 0, 0, 0]
    assert diag["position_freeze_bars_left"].tolist() == [0, 0, 0, 0, 0, 0]
    assert diag["position_freeze_ignored_opposite_st_flip"].tolist() == [
        0, 0, 0, 0, 0, 0
    ]
    assert set(diag["position_freeze_release_action"]) == {"none"}


def test_mode_d_position_freeze_ignores_opposite_flip_inside_window():
    result = apply(
        trend=np.array([1, 1, 1, -1, -1, -1, -1], dtype=np.int64),
        trade_mode="long",
        trade_filter_config=_cfg(
            ttl_bars=10,
            position_freeze_enabled=True,
            position_freeze_min_hold_bars=2,
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=7),
    )

    diag = result.filter_diagnostics
    assert result.positions.tolist() == [0, 0, 1, 1, 1, 0, 0]
    assert diag["position_freeze_active"].tolist() == [0, 0, 1, 1, 0, 0, 0]
    assert diag["position_freeze_bars_left"].tolist() == [0, 0, 2, 1, 0, 0, 0]
    assert int(diag["position_freeze_ignored_opposite_st_flip"][3]) == 1
    assert diag["wakeup_position_action"][3] == (
        "position_freeze_ignored_opposite_st_flip"
    )
    assert diag["position_freeze_release_action"][4] == (
        "applied_flat_on_disallowed_st_flip"
    )
    assert diag["wakeup_position_action"][4] == "flat_on_disallowed_st_flip"


def test_mode_d_position_freeze_release_preserves_open_to_open_bars_held_shift():
    trend = np.array([1, 1, 1, -1, -1, -1, -1], dtype=np.int64)
    result = apply(
        trend=trend,
        trade_mode="long",
        trade_filter_config=_cfg(
            ttl_bars=10,
            position_freeze_enabled=True,
            position_freeze_min_hold_bars=2,
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=7),
    )

    trades = extract_trades(
        result.positions,
        returns=np.zeros(6, dtype=np.float64),
        execution_prices=np.arange(100.0, 107.0, dtype=np.float64),
        index=pd.date_range("2026-01-01", periods=7, freq="min"),
        commission_rate=0.0,
        trend=trend,
    )

    assert result.positions.tolist() == [0, 0, 1, 1, 1, 0, 0]
    assert len(trades) == 1
    assert int(trades.iloc[0]["entry_index"]) == 2
    assert int(trades.iloc[0]["exit_index"]) == 5
    assert int(trades.iloc[0]["bars_held"]) == 3


def test_mode_d_position_freeze_opposite_flip_after_window_is_normal():
    result = apply(
        trend=np.array([1, 1, 1, 1, -1, -1], dtype=np.int64),
        trade_mode="long",
        trade_filter_config=_cfg(
            ttl_bars=10,
            position_freeze_enabled=True,
            position_freeze_min_hold_bars=1,
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=6),
    )

    diag = result.filter_diagnostics
    assert result.positions.tolist() == [0, 0, 1, 1, 1, 0]
    assert int(diag["position_freeze_ignored_opposite_st_flip"][4]) == 0
    assert diag["wakeup_position_action"][4] == "flat_on_disallowed_st_flip"
    assert set(diag["position_freeze_release_action"]) == {"none"}


def test_mode_d_position_freeze_realigns_before_release_without_noop_marker():
    result = apply(
        trend=np.array([1, 1, 1, -1, 1, 1, 1], dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_bars=10,
            position_freeze_enabled=True,
            position_freeze_min_hold_bars=3,
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=7),
    )

    diag = result.filter_diagnostics
    assert result.positions.tolist() == [0, 0, 1, 1, 1, 1, 1]
    assert int(diag["position_freeze_ignored_opposite_st_flip"][3]) == 1
    assert diag["wakeup_position_action"][4] == "none"
    assert set(diag["position_freeze_release_action"]) == {"none"}


def test_mode_d_position_freeze_release_reverses_and_starts_new_window():
    result = apply(
        trend=np.array([1, 1, -1, -1, -1, 1, 1, 1], dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_bars=10,
            position_freeze_enabled=True,
            position_freeze_min_hold_bars=1,
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=8),
    )

    diag = result.filter_diagnostics
    assert result.positions.tolist() == [0, 0, 1, 1, -1, -1, 1, 1]
    assert diag["position_freeze_release_action"][3] == (
        "applied_reverse_on_st_flip"
    )
    assert diag["wakeup_position_action"][3] == "reverse_on_st_flip"
    assert int(diag["position_freeze_active"][4]) == 1
    assert diag["wakeup_position_action"][5] == "reverse_on_st_flip"


def test_mode_d_position_freeze_release_noops_when_st_realigns_on_expiry_bar():
    result = apply(
        trend=np.array([1, 1, -1, 1, 1], dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_bars=10,
            position_freeze_enabled=True,
            position_freeze_min_hold_bars=1,
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=5),
    )

    diag = result.filter_diagnostics
    assert result.positions.tolist() == [0, 0, 1, 1, 1]
    assert diag["wakeup_position_action"][2] == (
        "position_freeze_ignored_opposite_st_flip"
    )
    assert diag["position_freeze_release_action"][3] == "noop_st_realigned"
    assert diag["wakeup_position_action"][3] == "none"


def test_mode_d_position_freeze_lock_cycle_release_flats_not_reverses():
    result = apply(
        trend=np.array([1, 1, -1, -1, -1, -1], dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_bars=10,
            lock_cycle_direction=True,
            position_freeze_enabled=True,
            position_freeze_min_hold_bars=1,
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=6),
    )

    diag = result.filter_diagnostics
    assert result.positions.tolist() == [0, 0, 1, 1, 0, 0]
    assert diag["position_freeze_release_action"][3] == (
        "applied_flat_on_disallowed_st_flip"
    )
    assert diag["wakeup_position_action"][3] == "flat_on_disallowed_st_flip"
    assert min(result.positions.tolist()) >= 0


def test_mode_d_position_freeze_invalid_lock_state_maps_to_no_effective_mode():
    assert _effective_wakeup_trade_mode(
        raw_trade_mode="both",
        wakeup_lock_cycle_direction=True,
        cycle_direction=0,
    ) is None


def test_mode_d_position_freeze_restore_does_not_start_new_window():
    result = apply(
        trend=np.array([1, 1, 1, -1, 1, -1, -1], dtype=np.int64),
        trade_mode="long",
        trade_filter_config=_cfg(
            ttl_bars=10,
            position_freeze_enabled=True,
            position_freeze_min_hold_bars=1,
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=7),
    )

    diag = result.filter_diagnostics
    assert result.positions.tolist() == [0, 0, 1, 1, 0, 1, 0]
    assert diag["wakeup_position_action"][3] == "flat_on_disallowed_st_flip"
    assert diag["wakeup_position_action"][4] == (
        "restore_allowed_position_on_st_flip"
    )
    assert int(diag["position_freeze_active"][5]) == 0
    assert diag["wakeup_position_action"][5] == "flat_on_disallowed_st_flip"


@pytest.mark.parametrize(
    ("action_mode", "expected_positions", "expected_state"),
    [
        ("block_new_entries", [0, 0, 1, 1, 1], "ST_STOPPING"),
        ("close_position", [0, 0, 1, 1, 0], "OFF"),
    ],
)
def test_mode_d_position_freeze_exit_c_beats_release_on_expiry_bar(
    action_mode,
    expected_positions,
    expected_state,
):
    result = apply(
        trend=np.array([1, 1, -1, -1, -1], dtype=np.int64),
        trade_mode="long",
        trade_filter_config=_cfg(
            ttl_bars=2,
            action_mode=action_mode,
            position_freeze_enabled=True,
            position_freeze_min_hold_bars=1,
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=5),
    )

    diag = result.filter_diagnostics
    assert result.positions.tolist() == expected_positions
    assert diag["wakeup_position_action"][2] == (
        "position_freeze_ignored_opposite_st_flip"
    )
    assert diag["wakeup_position_action"][3] == "exit_ttl"
    assert diag["position_freeze_release_action"][3] == "none"
    assert diag["trade_filter_state"][3] == expected_state


def test_mode_d_position_freeze_pending_cleared_by_reset_before_release():
    result = apply(
        trend=np.array([1, 1, -1, -1, -1], dtype=np.int64),
        trade_mode="long",
        trade_filter_config=_cfg(
            ttl_bars=10,
            position_freeze_enabled=True,
            position_freeze_min_hold_bars=2,
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=5),
        daily_reset_event=np.array([False, False, False, True, False]),
    )

    diag = result.filter_diagnostics
    assert result.positions.tolist() == [0, 0, 1, 1, 0]
    assert diag["wakeup_position_action"][2] == (
        "position_freeze_ignored_opposite_st_flip"
    )
    assert diag["wakeup_position_action"][3] == "exit_reset"
    assert set(diag["position_freeze_release_action"]) == {"none"}
    assert diag["trade_filter_state"][3] == "OFF"


def test_mode_d_position_freeze_last_bar_entry_has_no_bogus_release_or_write():
    result = apply(
        trend=np.array([0, 0], dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            position_freeze_enabled=True,
            position_freeze_min_hold_bars=1,
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=2),
    )

    diag = result.filter_diagnostics
    assert result.positions.tolist() == [0, 0]
    assert diag["trade_filter_state"][1] == "ST_ACTIVE_FREEZE"
    assert diag["position_freeze_active"].tolist() == [0, 0]
    assert set(diag["position_freeze_release_action"]) == {"none"}


def test_mode_d_short_internal_st_flip_flats_then_restores_active_cycle():
    result = apply(
        trend=np.array([-1, -1, -1, -1, 1, -1, -1], dtype=np.int64),
        trade_mode="short",
        trade_filter_config=_cfg(ttl_bars=10),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, direction=-1, n=7),
    )

    diag = result.filter_diagnostics
    assert result.positions.tolist() == [0, 0, -1, -1, -1, 0, -1]
    assert diag["wakeup_exit_reason"][4] == "none"
    assert diag["wakeup_position_action"][4] == "flat_on_disallowed_st_flip"
    assert int(diag["wakeup_active_direction"][4]) == -1
    assert int(diag["wakeup_regime_active"][4]) == 1
    assert diag["wakeup_exit_reason"][5] == "none"
    assert diag["wakeup_position_action"][5] == (
        "restore_allowed_position_on_st_flip"
    )
    assert int(diag["wakeup_active_direction"][5]) == -1
    assert int(diag["wakeup_regime_active"][5]) == 1
    assert diag["trade_filter_state"][5] == "ST_ACTIVE_FREEZE"


def test_mode_d_lock_long_cycle_flats_then_restores_without_reversing():
    result = apply(
        trend=np.array([1, 1, 1, 1, -1, 1, 1], dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(ttl_bars=10, lock_cycle_direction=True),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=7),
    )

    diag = result.filter_diagnostics
    assert result.positions.tolist() == [0, 0, 1, 1, 1, 0, 1]
    assert min(result.positions.tolist()[2:]) >= 0
    assert diag["wakeup_position_action"][4] == "flat_on_disallowed_st_flip"
    assert int(diag["wakeup_active_direction"][4]) == 1
    assert diag["wakeup_position_action"][5] == (
        "restore_allowed_position_on_st_flip"
    )
    assert int(diag["wakeup_active_direction"][5]) == 1
    assert diag["trade_filter_state"][5] == "ST_ACTIVE_FREEZE"


def test_mode_d_lock_short_cycle_flats_then_restores_without_reversing():
    result = apply(
        trend=np.array([-1, -1, -1, -1, 1, -1, -1], dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(ttl_bars=10, lock_cycle_direction=True),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, direction=-1, n=7),
    )

    diag = result.filter_diagnostics
    assert result.positions.tolist() == [0, 0, -1, -1, -1, 0, -1]
    assert max(result.positions.tolist()[2:]) <= 0
    assert diag["wakeup_position_action"][4] == "flat_on_disallowed_st_flip"
    assert int(diag["wakeup_active_direction"][4]) == -1
    assert diag["wakeup_position_action"][5] == (
        "restore_allowed_position_on_st_flip"
    )
    assert int(diag["wakeup_active_direction"][5]) == -1
    assert diag["trade_filter_state"][5] == "ST_ACTIVE_FREEZE"


def test_mode_d_lock_new_cycle_can_start_opposite_direction_after_off():
    per_bar = _per_bar(t=1, n=8)
    per_bar.candidate_height_pct[4] = 0.12
    per_bar.candidate_age_bars[4] = 3
    per_bar.candidate_leg_direction[4] = -1

    result = apply(
        trend=np.zeros(8, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_bars=1,
            action_mode="close_position",
            lock_cycle_direction=True,
        ),
        zigzag_global_stats=_stats(),
        per_bar=per_bar,
    )

    diag = result.filter_diagnostics
    assert result.positions.tolist() == [0, 0, 1, 0, 0, -1, 0, 0]
    assert diag["trade_filter_state"][2] == "OFF"
    assert diag["trade_filter_trigger_source"][1] == "wakeup_regime"
    assert diag["trade_filter_trigger_source"][4] == "wakeup_regime"
    assert int(diag["wakeup_active_direction"][1]) == 1
    assert int(diag["wakeup_active_direction"][4]) == -1
    assert int(diag["wakeup_lock_cycle_direction_config"][4]) == 1


@pytest.mark.parametrize(
    ("lock_cycle_direction", "expected_active_direction", "expected_since_fresh"),
    [
        (False, -1, 0),
        (True, 1, 4),
    ],
)
def test_mode_d_no_fresh_reference_follows_lock_semantics(
    lock_cycle_direction,
    expected_active_direction,
    expected_since_fresh,
):
    per_bar = _per_bar(t=1, n=7, age=2)
    per_bar.candidate_height_pct[5] = 0.12
    per_bar.candidate_age_bars[5] = 1
    per_bar.candidate_leg_direction[5] = -1

    result = apply(
        trend=np.array([1, 1, 1, 1, -1, -1, -1], dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            no_fresh_enabled=True,
            no_fresh_timeout_bars=10,
            lock_cycle_direction=lock_cycle_direction,
        ),
        zigzag_global_stats=_stats(no_fresh_threshold=0.10),
        per_bar=per_bar,
    )

    diag = result.filter_diagnostics
    assert int(diag["wakeup_active_direction"][5]) == expected_active_direction
    assert int(diag["wakeup_bars_since_fresh_candidate"][5]) == (
        expected_since_fresh
    )


def test_mode_d_wakeup_diagnostics_keyset_lengths_and_dtypes():
    result = apply(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1),
    )

    diag = result.filter_diagnostics
    wakeup_dtypes = {
        "wakeup_regime_active": np.int8,
        "wakeup_entry_all_ok": np.int8,
        "wakeup_entry_candidate_height_ok": np.int8,
        "wakeup_entry_candidate_age_ok": np.int8,
        "wakeup_entry_candidate_direction_ok": np.int8,
        "wakeup_entry_trade_mode_ok": np.int8,
        "wakeup_entry_atr_ok": np.int8,
        "wakeup_entry_volume_ok": np.int8,
        "wakeup_entry_candidate_height_value": np.float64,
        "wakeup_entry_candidate_height_threshold": np.float64,
        "wakeup_entry_candidate_age_bars": np.int64,
        "wakeup_entry_candidate_leg_direction": np.int8,
        "wakeup_entry_atr_ratio": np.float64,
        "wakeup_entry_volume_ratio": np.float64,
        "wakeup_cycle_age_bars": np.int64,
        "wakeup_bars_since_fresh_candidate": np.int64,
        "wakeup_cycle_trade_count": np.int64,
        "wakeup_cycle_realized_pnl_pct": np.float64,
        "wakeup_exit_cycle_take_profit_triggered": np.int8,
        "wakeup_exit_ttl_triggered": np.int8,
        "wakeup_exit_no_fresh_candidate_triggered": np.int8,
        "wakeup_exit_local_median_stop_triggered": np.int8,
        "wakeup_exit_cycle_trade_limit_triggered": np.int8,
        "wakeup_exit_close_triggered": np.int8,
        "wakeup_exit_action_mode": object,
        "wakeup_exit_reason": object,
        "wakeup_position_action": object,
        "wakeup_active_direction": np.int8,
        "wakeup_lock_cycle_direction_config": np.int8,
        "wakeup_cycle_trade_limit_config": np.int64,
        "position_freeze_active": np.int8,
        "position_freeze_bars_left": np.int64,
        "position_freeze_ignored_opposite_st_flip": np.int8,
        "position_freeze_release_action": object,
    }
    assert set(wakeup_dtypes).issubset(diag)
    for key, dtype in wakeup_dtypes.items():
        arr = diag[key]
        assert len(arr) == len(result.positions), key
        assert arr.dtype == dtype, key

    assert set(diag["wakeup_exit_action_mode"]) == {"block_new_entries"}
    assert set(diag["wakeup_exit_reason"]).issubset(
        {
            "none",
            "ttl",
            "no_fresh_candidate",
            "local_median_stop",
            "cycle_trade_limit",
            "reset",
        }
    )
    assert set(diag["wakeup_position_action"]).issubset(
        {
            "none",
            "reverse_on_st_flip",
            "flat_on_disallowed_st_flip",
            "restore_allowed_position_on_st_flip",
            "position_freeze_ignored_opposite_st_flip",
            "exit_ttl",
            "exit_no_fresh_candidate",
            "exit_local_median_stop",
            "exit_cycle_trade_limit",
            "exit_reset",
        }
    )
    assert set(diag["position_freeze_release_action"]).issubset({
        "none",
        "noop_st_realigned",
        "noop_invalid_lock_state",
        "applied_flat_on_disallowed_st_flip",
        "applied_reverse_on_st_flip",
        "applied_restore_allowed_position_on_st_flip",
    })
    assert set(diag["wakeup_active_direction"]).issubset({-1, 0, 1})
    assert set(diag["wakeup_lock_cycle_direction_config"]) == {0}
    assert set(diag["wakeup_cycle_trade_limit_config"]) == {0}
    assert int(diag["wakeup_entry_all_ok"][1]) == 1
    assert int(diag["wakeup_cycle_trade_count"][0]) == -1
    assert int(diag["wakeup_cycle_trade_count"][1]) == 1
    assert diag["wakeup_entry_candidate_height_value"][1] == pytest.approx(0.12)
    assert diag["wakeup_entry_candidate_height_threshold"][1] == pytest.approx(0.10)
    assert int(diag["wakeup_entry_candidate_age_bars"][1]) == 3
    assert int(diag["wakeup_entry_candidate_leg_direction"][1]) == 1


@pytest.mark.parametrize(
    ("cfg", "expected"),
    [
        (_cfg(lock_cycle_direction=True), 1),
        (_cfg(lock_cycle_direction=False), 0),
        (
            SimpleNamespace(
                lifecycle=_cfg().lifecycle,
                wakeup_regime=SimpleNamespace(
                    enabled=True,
                    entry=_cfg().wakeup_regime.entry,
                    exit=_cfg().wakeup_regime.exit,
                ),
            ),
            0,
        ),
    ],
)
def test_mode_d_wakeup_lock_cycle_direction_config_echo(cfg, expected):
    result = apply(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=cfg,
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1),
    )

    diag = result.filter_diagnostics
    assert set(diag["wakeup_lock_cycle_direction_config"]) == {expected}


def test_mode_d_wakeup_diagnostics_keyset_includes_only_expected_wakeup_keys():
    result = apply(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(lock_cycle_direction=True),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1),
    )
    non_d_result = apply(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(lock_cycle_direction=True),
        zigzag_global_stats=_stats_for_mode("A"),
        per_bar=_per_bar(t=1),
    )

    wakeup_keys = {key for key in result.filter_diagnostics if key.startswith("wakeup_")}
    assert wakeup_keys == MODE_D_WAKEUP_EXPECTED_KEYS
    assert MODE_D_POSITION_FREEZE_EXPECTED_KEYS.issubset(result.filter_diagnostics)
    assert set(result.filter_diagnostics) == (
        set(non_d_result.filter_diagnostics)
        | MODE_D_WAKEUP_EXPECTED_KEYS
        | MODE_D_POSITION_FREEZE_EXPECTED_KEYS
    )


def test_mode_d_trade_diagnostics_expose_cycle_reason_and_position_action():
    trades = pd.DataFrame(
        {
            "trade_id": [1],
            "entry_index": [1],
            "exit_index": [3],
        }
    )
    diag = {
        "trade_filter_state": np.array(
            ["OFF", "ST_ACTIVE_FREEZE", "ST_ACTIVE_FREEZE", "OFF", "OFF"],
            dtype=object,
        ),
        "trade_filter_trigger_source": np.array(["none"] * 5, dtype=object),
        "wakeup_exit_reason": np.array(["none"] * 5, dtype=object),
        "wakeup_position_action": np.array(
            ["none", "none", "reverse_on_st_flip", "none", "none"],
            dtype=object,
        ),
    }

    enriched = attach_trade_filter_diagnostics(trades, diag)

    assert enriched["wakeup_cycle_exit_reason"].iloc[0] == "none"
    assert enriched["wakeup_position_action"].iloc[0] == "reverse_on_st_flip"
    assert enriched["exit_reason"].iloc[0] == "wakeup_reverse_on_st_flip"


def test_mode_d_trade_diagnostics_expose_local_median_stop_reason():
    trades = pd.DataFrame(
        {
            "trade_id": [1],
            "entry_index": [1],
            "exit_index": [3],
        }
    )
    diag = {
        "trade_filter_state": np.array(
            ["OFF", "ST_ACTIVE_FREEZE", "ST_ACTIVE_FREEZE", "OFF", "OFF"],
            dtype=object,
        ),
        "trade_filter_trigger_source": np.array(["none"] * 5, dtype=object),
        "wakeup_exit_reason": np.array(
            ["none", "none", "local_median_stop", "none", "none"],
            dtype=object,
        ),
        "wakeup_position_action": np.array(["none"] * 5, dtype=object),
    }

    enriched = attach_trade_filter_diagnostics(trades, diag)

    assert enriched["wakeup_cycle_exit_reason"].iloc[0] == "local_median_stop"
    assert enriched["exit_reason"].iloc[0] == "wakeup_exit_local_median_stop"


def test_mode_d_trade_diagnostics_expose_local_median_stop_position_action():
    trades = pd.DataFrame(
        {
            "trade_id": [1],
            "entry_index": [1],
            "exit_index": [3],
        }
    )
    diag = {
        "trade_filter_state": np.array(
            ["OFF", "ST_ACTIVE_FREEZE", "ST_ACTIVE_FREEZE", "OFF", "OFF"],
            dtype=object,
        ),
        "trade_filter_trigger_source": np.array(["none"] * 5, dtype=object),
        "wakeup_exit_reason": np.array(["none"] * 5, dtype=object),
        "wakeup_position_action": np.array(
            ["none", "none", "exit_local_median_stop", "none", "none"],
            dtype=object,
        ),
    }

    enriched = attach_trade_filter_diagnostics(trades, diag)

    assert enriched["wakeup_position_action"].iloc[0] == (
        "exit_local_median_stop"
    )
    assert enriched["exit_reason"].iloc[0] == "wakeup_exit_local_median_stop"


def test_non_mode_d_output_does_not_include_mode_d_wakeup_diagnostics():
    result = apply(
        trend=np.array([1, 1, -1, -1, 1, 1], dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(),
        zigzag_global_stats=_stats_for_mode("A"),
        per_bar=_per_bar(t=1),
    )

    diag = result.filter_diagnostics
    assert diag["zigzag_mode"][0] == "A"
    assert "wakeup_position_action" not in diag
    assert "wakeup_active_direction" not in diag
    assert "wakeup_lock_cycle_direction_config" not in diag
    assert "wakeup_cycle_trade_count" not in diag
    assert "wakeup_exit_cycle_trade_limit_triggered" not in diag
    assert "wakeup_exit_local_median_stop_triggered" not in diag
    assert "wakeup_cycle_trade_limit_config" not in diag
    assert "wakeup_exit_reason" not in diag


def test_mode_d_tester_summary_is_wakeup_mode_aware():
    cfg = _cfg(ttl_bars=1, action_mode="close_position")
    stats = _stats()
    result = apply(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=cfg,
        zigzag_global_stats=stats,
        per_bar=_per_bar(t=1),
    )
    root_cfg = SimpleNamespace(
        enabled=True,
        zigzag=SimpleNamespace(
            enabled=True,
            reversal_threshold=0.01,
            local_window=2,
        ),
        lifecycle=cfg.lifecycle,
        wakeup_regime=cfg.wakeup_regime,
    )
    bt_result = SimpleNamespace(
        filter_diagnostics=result.filter_diagnostics,
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="both",
        positions=result.positions,
        trades_df=None,
    )

    summary = _build_filter_diagnostics_summary(
        bt_result,
        root_cfg,
        stats,
        global_offset=0,
    )

    assert summary["zigzag_mode"] == "D"
    assert summary["exit_off_mode"] == "exit C"
    assert summary["wakeup_enabled"] is True
    assert summary["wakeup_exit_action_mode"] == "close_position"
    assert summary["wakeup_starts_count"] == 1
    assert summary["wakeup_entry_attempts_count"] == 1
    assert summary["wakeup_exit_ttl_count"] == 1
    assert summary["wakeup_exit_local_median_stop_count"] == 0
    assert summary["wakeup_exit_close_count"] == 1
    assert summary["wakeup_reverse_on_st_flip_count"] == 0
    assert summary["wakeup_flat_on_disallowed_st_flip_count"] == 0
    assert summary["wakeup_restore_allowed_position_on_st_flip_count"] == 0
    assert summary["wakeup_position_freeze_ignored_opposite_st_flip_count"] == 0
    assert summary["wakeup_position_freeze_release_flat_count"] == 0
    assert summary["wakeup_position_freeze_release_reverse_count"] == 0
    assert summary["wakeup_position_freeze_release_noop_count"] == 0
    assert summary["wakeup_bars_active"] == 2
    assert summary["trigger_count_candidate_threshold"] == 0
    assert summary["trigger_count_confirmed_median"] == 0
    assert summary["trigger_count_both"] == 0
    assert summary["median_stop_triggered_count"] == 0
    assert summary["zz_leg_stop_triggered_count"] == 0
    assert summary["thresholds"]["wakeup_entry_candidate_height_threshold"] == 0.10
    assert summary["thresholds"]["wakeup_entry_candidate_height_quantile"] == 0.65
    assert summary["thresholds"]["wakeup_ttl_bars"] == 1


def test_mode_d_tester_summary_counts_position_actions():
    cfg = _cfg(ttl_bars=10)
    stats = _stats()
    result = apply(
        trend=np.array([1, 1, 1, 1, -1, 1, 1], dtype=np.int64),
        trade_mode="long",
        trade_filter_config=cfg,
        zigzag_global_stats=stats,
        per_bar=_per_bar(t=1, n=7),
    )
    root_cfg = SimpleNamespace(
        enabled=True,
        zigzag=SimpleNamespace(
            enabled=True,
            reversal_threshold=0.01,
            local_window=2,
        ),
        lifecycle=cfg.lifecycle,
        wakeup_regime=cfg.wakeup_regime,
    )
    bt_result = SimpleNamespace(
        filter_diagnostics=result.filter_diagnostics,
        trend=np.zeros(7, dtype=np.int64),
        trade_mode="long",
        positions=result.positions,
        trades_df=None,
    )

    summary = _build_filter_diagnostics_summary(
        bt_result,
        root_cfg,
        stats,
        global_offset=0,
    )

    assert summary["wakeup_reverse_on_st_flip_count"] == 0
    assert summary["wakeup_flat_on_disallowed_st_flip_count"] == 1
    assert summary["wakeup_restore_allowed_position_on_st_flip_count"] == 1
    assert summary["wakeup_exit_opposite_st_flip_count"] == 0
    assert summary["wakeup_position_freeze_ignored_opposite_st_flip_count"] == 0
    assert summary["wakeup_position_freeze_release_flat_count"] == 0
    assert summary["wakeup_position_freeze_release_reverse_count"] == 0
    assert summary["wakeup_position_freeze_release_noop_count"] == 0


def test_mode_d_cycle_trade_limit_summary_builders_emit_no_new_counter():
    cfg = _cfg(
        ttl_enabled=False,
        max_trades_enabled=True,
        max_trades=1,
        action_mode="close_position",
    )
    stats = _stats()
    result = apply(
        trend=np.zeros(5, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=cfg,
        zigzag_global_stats=stats,
        per_bar=_per_bar(t=1, n=5),
    )
    root_cfg = SimpleNamespace(
        enabled=True,
        zigzag=SimpleNamespace(
            enabled=True,
            reversal_threshold=0.01,
            local_window=2,
        ),
        lifecycle=cfg.lifecycle,
        wakeup_regime=cfg.wakeup_regime,
    )
    bt_result = SimpleNamespace(
        filter_diagnostics=result.filter_diagnostics,
        trend=np.zeros(5, dtype=np.int64),
        trade_mode="both",
        positions=result.positions,
        trades_df=None,
    )

    summary = _build_filter_diagnostics_summary(
        bt_result,
        root_cfg,
        stats,
        global_offset=0,
    )
    filters_summary = _build_filters_summary_df(
        [SimpleNamespace(period_label="100%", filter_diagnostics_summary=summary)]
    )

    assert "cycle_trade_limit" in set(result.filter_diagnostics["wakeup_exit_reason"])
    assert "exit_cycle_trade_limit" in set(
        result.filter_diagnostics["wakeup_position_action"]
    )
    assert summary["wakeup_exit_close_count"] == 1
    assert "wakeup_exit_cycle_trade_limit_count" not in summary
    assert filters_summary is not None
    params_df, period_df = filters_summary
    assert "wakeup_exit_cycle_trade_limit_count" not in set(params_df.iloc[:, 0])
    assert "wakeup_exit_cycle_trade_limit_count" not in set(period_df.columns)


def test_mode_d_local_median_stop_summary_counts_reason_and_close_action():
    cfg = _cfg(
        ttl_enabled=False,
        local_median_stop_enabled=True,
        action_mode="close_position",
    )
    stats = _stats()
    result = apply(
        trend=np.zeros(5, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=cfg,
        zigzag_global_stats=stats,
        per_bar=_per_bar_with_lms(lms_bar=2, n=5),
    )
    root_cfg = SimpleNamespace(
        enabled=True,
        zigzag=SimpleNamespace(
            enabled=True,
            reversal_threshold=0.01,
            local_window=2,
        ),
        lifecycle=cfg.lifecycle,
        wakeup_regime=cfg.wakeup_regime,
    )
    bt_result = SimpleNamespace(
        filter_diagnostics=result.filter_diagnostics,
        trend=np.zeros(5, dtype=np.int64),
        trade_mode="both",
        positions=result.positions,
        trades_df=None,
    )

    summary = _build_filter_diagnostics_summary(
        bt_result,
        root_cfg,
        stats,
        global_offset=0,
    )

    assert summary["wakeup_exit_local_median_stop_count"] == 1
    assert summary["wakeup_exit_close_count"] == 1


def test_mode_d_tester_summary_counts_locked_both_position_actions():
    cfg = _cfg(ttl_bars=10, lock_cycle_direction=True)
    stats = _stats()
    result = apply(
        trend=np.array([1, 1, 1, 1, -1, 1, 1], dtype=np.int64),
        trade_mode="both",
        trade_filter_config=cfg,
        zigzag_global_stats=stats,
        per_bar=_per_bar(t=1, n=7),
    )
    root_cfg = SimpleNamespace(
        enabled=True,
        zigzag=SimpleNamespace(
            enabled=True,
            reversal_threshold=0.01,
            local_window=2,
        ),
        lifecycle=cfg.lifecycle,
        wakeup_regime=cfg.wakeup_regime,
    )
    bt_result = SimpleNamespace(
        filter_diagnostics=result.filter_diagnostics,
        trend=np.zeros(7, dtype=np.int64),
        trade_mode="both",
        positions=result.positions,
        trades_df=None,
    )

    summary = _build_filter_diagnostics_summary(
        bt_result,
        root_cfg,
        stats,
        global_offset=0,
    )

    assert summary["wakeup_reverse_on_st_flip_count"] == 0
    assert summary["wakeup_flat_on_disallowed_st_flip_count"] == 1
    assert summary["wakeup_restore_allowed_position_on_st_flip_count"] == 1
    assert summary["wakeup_exit_opposite_st_flip_count"] == 0


def test_mode_d_tester_summary_counts_position_freeze_release_actions():
    cfg = _cfg(
        ttl_bars=10,
        position_freeze_enabled=True,
        position_freeze_min_hold_bars=1,
    )
    stats = _stats()
    result = apply(
        trend=np.array([1, 1, -1, -1, -1, -1], dtype=np.int64),
        trade_mode="long",
        trade_filter_config=cfg,
        zigzag_global_stats=stats,
        per_bar=_per_bar(t=1, n=6),
    )
    root_cfg = SimpleNamespace(
        enabled=True,
        zigzag=SimpleNamespace(
            enabled=True,
            reversal_threshold=0.01,
            local_window=2,
        ),
        lifecycle=cfg.lifecycle,
        wakeup_regime=cfg.wakeup_regime,
    )
    bt_result = SimpleNamespace(
        filter_diagnostics=result.filter_diagnostics,
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="long",
        positions=result.positions,
        trades_df=None,
    )

    summary = _build_filter_diagnostics_summary(
        bt_result,
        root_cfg,
        stats,
        global_offset=0,
    )

    assert summary["wakeup_flat_on_disallowed_st_flip_count"] == 1
    assert summary["wakeup_position_freeze_ignored_opposite_st_flip_count"] == 1
    assert summary["wakeup_position_freeze_release_flat_count"] == 1
    assert summary["wakeup_position_freeze_release_reverse_count"] == 0
    assert summary["wakeup_position_freeze_release_noop_count"] == 0


def test_mode_d_excel_display_names_and_filters_summary_rows():
    wakeup_keys = {
        "wakeup_regime_active",
        "wakeup_entry_all_ok",
        "wakeup_entry_candidate_height_ok",
        "wakeup_entry_candidate_age_ok",
        "wakeup_entry_candidate_direction_ok",
        "wakeup_entry_trade_mode_ok",
        "wakeup_entry_atr_ok",
        "wakeup_entry_volume_ok",
        "wakeup_entry_candidate_height_value",
        "wakeup_entry_candidate_height_threshold",
        "wakeup_entry_candidate_age_bars",
        "wakeup_entry_candidate_leg_direction",
        "wakeup_entry_atr_ratio",
        "wakeup_entry_volume_ratio",
        "wakeup_cycle_age_bars",
        "wakeup_bars_since_fresh_candidate",
        "wakeup_cycle_trade_count",
        "wakeup_exit_ttl_triggered",
        "wakeup_exit_no_fresh_candidate_triggered",
        "wakeup_exit_local_median_stop_triggered",
        "wakeup_exit_cycle_trade_limit_triggered",
        "wakeup_exit_close_triggered",
        "wakeup_exit_action_mode",
        "wakeup_exit_reason",
        "wakeup_position_action",
        "wakeup_active_direction",
        "wakeup_lock_cycle_direction_config",
        "wakeup_cycle_trade_limit_config",
    }
    assert wakeup_keys.issubset(FILTER_DIAGNOSTICS_100_DISPLAY_NAMES)
    assert (
        FILTER_DIAGNOSTICS_100_DISPLAY_NAMES["wakeup_position_action"]
        == "Wakeup Position Action"
    )
    assert (
        FILTER_DIAGNOSTICS_100_DISPLAY_NAMES["wakeup_active_direction"]
        == "Wakeup Active Direction"
    )
    assert (
        FILTER_DIAGNOSTICS_100_DISPLAY_NAMES["wakeup_lock_cycle_direction_config"]
        == "Wakeup Lock Cycle Direction Config"
    )
    assert (
        FILTER_DIAGNOSTICS_100_DISPLAY_NAMES["wakeup_cycle_trade_count"]
        == "Wakeup Cycle Trade Count"
    )
    assert (
        FILTER_DIAGNOSTICS_100_DISPLAY_NAMES[
            "wakeup_exit_local_median_stop_triggered"
        ]
        == "Wakeup Exit Local Median Stop Triggered"
    )
    assert (
        FILTER_DIAGNOSTICS_100_DISPLAY_NAMES[
            "wakeup_exit_cycle_trade_limit_triggered"
        ]
        == "Wakeup Exit Cycle Trade Limit Triggered"
    )
    assert (
        FILTER_DIAGNOSTICS_100_DISPLAY_NAMES["wakeup_cycle_trade_limit_config"]
        == "Wakeup Cycle Trade Limit Config"
    )
    assert (
        FILTER_DIAGNOSTICS_100_DISPLAY_NAMES["position_freeze_release_action"]
        == "Wakeup Position Freeze Release Action"
    )

    summary = {
        "zigzag_mode": "D",
        "wakeup_enabled": True,
        "wakeup_exit_action_mode": "block_new_entries",
        "wakeup_starts_count": 2,
        "wakeup_entry_attempts_count": 3,
        "wakeup_exit_ttl_count": 1,
        "wakeup_exit_cycle_take_profit_count": 9,
        "wakeup_exit_no_fresh_candidate_count": 1,
        "wakeup_exit_local_median_stop_count": 2,
        "wakeup_exit_close_count": 0,
        "wakeup_exit_reset_count": 1,
        "wakeup_exit_opposite_st_flip_count": 1,
        "wakeup_reverse_on_st_flip_count": 2,
        "wakeup_flat_on_disallowed_st_flip_count": 3,
        "wakeup_restore_allowed_position_on_st_flip_count": 4,
        "wakeup_position_freeze_ignored_opposite_st_flip_count": 5,
        "wakeup_position_freeze_release_flat_count": 6,
        "wakeup_position_freeze_release_reverse_count": 7,
        "wakeup_position_freeze_release_noop_count": 8,
        "wakeup_bars_active": 5,
        "thresholds": {
            "wakeup_entry_candidate_height_threshold": 0.10,
            "wakeup_no_fresh_candidate_height_threshold": 0.20,
            "wakeup_entry_candidate_height_quantile": 0.65,
            "wakeup_no_fresh_candidate_quantile": 0.60,
            "wakeup_candidate_age_max_bars": 4,
            "wakeup_atr_short_window": 2,
            "wakeup_atr_long_window": 4,
            "wakeup_atr_min_ratio": 1.0,
            "wakeup_volume_short_window": 2,
            "wakeup_volume_baseline_window": 3,
            "wakeup_volume_min_ratio": 1.0,
            "wakeup_ttl_bars": 10,
            "wakeup_no_fresh_max_age_bars": 2,
            "wakeup_no_fresh_timeout_bars": 3,
        },
    }
    params_df, period_df = _build_filters_summary_df([
        SimpleNamespace(period_label="100%", filter_diagnostics_summary=summary)
    ])

    params = dict(zip(params_df["Parameter"], params_df["Value"]))
    assert params["Wakeup Enabled"] is True
    assert params["Wakeup Exit Action Mode"] == "block_new_entries"
    assert params["Wakeup Entry Candidate Height Threshold"] == 0.10
    assert params["Wakeup TTL Bars"] == 10
    row = period_df.iloc[0]
    assert row["Wakeup Starts"] == 2
    assert row["Wakeup Entry Attempts"] == 3
    assert row["Wakeup Exit TTL"] == 1
    assert row["Wakeup Exit Cycle Take Profit"] == 9
    assert row["Wakeup Exit No Fresh Candidate"] == 1
    assert row["Wakeup Exit Local Median Stop"] == 2
    assert row["Wakeup Exit Close"] == 0
    assert row["Wakeup Exit Reset"] == 1
    assert row["Wakeup Exit Opposite ST Flip"] == 1
    assert row["Wakeup Reverse On ST Flip"] == 2
    assert row["Wakeup Flat On Disallowed ST Flip"] == 3
    assert row["Wakeup Restore Allowed Position On ST Flip"] == 4
    assert row["Wakeup Position Freeze Ignored Opposite ST Flip"] == 5
    assert row["Wakeup Position Freeze Release Flat"] == 6
    assert row["Wakeup Position Freeze Release Reverse"] == 7
    assert row["Wakeup Position Freeze Release Noop"] == 8
    assert row["Wakeup Bars Active"] == 5

    non_d_params_df, non_d_period_df = _build_filters_summary_df([
        SimpleNamespace(
            period_label="100%",
            filter_diagnostics_summary={
                "zigzag_mode": "A",
                "thresholds": {},
            },
        )
    ])
    assert "Wakeup Enabled" not in set(non_d_params_df["Parameter"])
    assert "Wakeup Starts" not in set(non_d_period_df.columns)


def test_mode_d_zigzag_trigger_events_use_wakeup_branch_and_link_trade():
    result = apply(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1),
    )
    trades = pd.DataFrame(
        {
            "trade_id": [42],
            "entry_index": [2],
            "exit_index": [5],
        }
    )

    trigger_df = _build_zigzag_trigger_events_df(
        result.filter_diagnostics,
        filter_diagnostics_summary={
            "thresholds": {
                "wakeup_entry_candidate_height_quantile": 0.65,
            }
        },
        trades_df=trades,
    )

    assert len(trigger_df) == 1
    row = trigger_df.iloc[0]
    assert row["Trigger Bar"] == 1
    assert row["Trigger Source"] == "wakeup_regime"
    assert bool(row["Triggered Lifecycle Start"]) is True
    assert row["Threshold Used"] == pytest.approx(0.10)
    assert row["Quantile Used"] == 0.65
    assert row["Candidate Height %"] == pytest.approx(0.12)
    assert row["Candidate Age Bars"] == 3
    assert row["Candidate Leg Direction"] == 1
    assert row["Immediate Candidate Entry Used"] == 0
    assert row["Immediate Candidate Entry Block Reason"] == "mode_not_c"
    assert row["Linked Trade ID"] == 42


@pytest.mark.parametrize(
    ("raw_reason", "expected"),
    [
        ("ttl", "wakeup_exit_ttl"),
        ("no_fresh_candidate", "wakeup_exit_no_fresh_candidate"),
        ("cycle_trade_limit", "wakeup_exit_cycle_trade_limit"),
        ("cycle_take_profit", "wakeup_exit_cycle_take_profit"),
        ("reset", "wakeup_exit_reset"),
        ("opposite_st_flip", "wakeup_exit_opposite_st_flip"),
    ],
)
def test_mode_d_trade_exit_reason_maps_wakeup_decision_bar(raw_reason, expected):
    trades = pd.DataFrame(
        {
            "trade_id": [1],
            "entry_index": [1],
            "exit_index": [3],
        }
    )
    diag = {
        "trade_filter_state": np.array(
            ["OFF", "ST_ACTIVE_FREEZE", "OFF", "OFF", "OFF"], dtype=object
        ),
        "trade_filter_trigger_source": np.array(["none"] * 5, dtype=object),
        "daily_reset_event": np.array([0, 0, 1, 0, 0], dtype=np.int8),
        "wakeup_exit_reason": np.array(
            ["none", "none", raw_reason, "none", "none"], dtype=object
        ),
    }

    for attach in (
        attach_trade_filter_diagnostics,
        attach_trade_filter_diagnostics_public,
    ):
        enriched = attach(trades, diag)
        assert enriched["exit_reason"].iloc[0] == expected


def test_mode_d_trade_exit_reason_none_preserves_legacy_mapping():
    trades = pd.DataFrame(
        {
            "trade_id": [1],
            "entry_index": [1],
            "exit_index": [3],
        }
    )
    diag = {
        "trade_filter_state": np.array(
            ["OFF", "ST_ACTIVE_FREEZE", "ST_STOPPING", "OFF", "OFF"],
            dtype=object,
        ),
        "trade_filter_trigger_source": np.array(["none"] * 5, dtype=object),
        "wakeup_exit_reason": np.array(["none"] * 5, dtype=object),
    }

    enriched = attach_trade_filter_diagnostics(trades, diag)
    assert enriched["exit_reason"].iloc[0] == "filter_stopping_opposite_flip"


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("reverse_on_st_flip", "wakeup_reverse_on_st_flip"),
        ("flat_on_disallowed_st_flip", "wakeup_flat_on_disallowed_st_flip"),
        ("exit_cycle_trade_limit", "wakeup_exit_cycle_trade_limit"),
        ("exit_cycle_take_profit", "wakeup_exit_cycle_take_profit"),
        ("restore_allowed_position_on_st_flip", "st_flip"),
        ("position_freeze_ignored_opposite_st_flip", "st_flip"),
    ],
)
def test_mode_d_trade_exit_reason_maps_position_action(action, expected):
    trades = pd.DataFrame(
        {
            "trade_id": [1],
            "entry_index": [1],
            "exit_index": [3],
        }
    )
    diag = {
        "trade_filter_state": np.array(
            ["OFF", "ST_ACTIVE_FREEZE", "ST_ACTIVE_FREEZE", "OFF", "OFF"],
            dtype=object,
        ),
        "trade_filter_trigger_source": np.array(["none"] * 5, dtype=object),
        "wakeup_exit_reason": np.array(["none"] * 5, dtype=object),
        "wakeup_position_action": np.array(
            ["none", "none", action, "none", "none"], dtype=object
        ),
    }

    enriched = attach_trade_filter_diagnostics(trades, diag)
    assert enriched["exit_reason"].iloc[0] == expected


def test_mode_d_trade_exit_reason_reset_has_priority_over_position_action():
    trades = pd.DataFrame(
        {
            "trade_id": [1],
            "entry_index": [1],
            "exit_index": [3],
        }
    )
    diag = {
        "trade_filter_state": np.array(
            ["OFF", "ST_ACTIVE_FREEZE", "OFF", "OFF", "OFF"], dtype=object
        ),
        "trade_filter_trigger_source": np.array(["none"] * 5, dtype=object),
        "daily_reset_event": np.array([0, 0, 1, 0, 0], dtype=np.int8),
        "wakeup_exit_reason": np.array(["none"] * 5, dtype=object),
        "wakeup_position_action": np.array(
            ["none", "none", "reverse_on_st_flip", "none", "none"], dtype=object
        ),
    }

    enriched = attach_trade_filter_diagnostics(trades, diag)
    assert enriched["exit_reason"].iloc[0] == "filter_daily_reset"


@pytest.mark.parametrize("lock_cycle_direction", [False, True])
def test_mode_d_trade_exit_reason_exit_c_beats_same_bar_st_flip_action(
    lock_cycle_direction,
):
    result = apply(
        trend=np.array([1, 1, -1, -1, -1], dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_bars=1,
            action_mode="close_position",
            lock_cycle_direction=lock_cycle_direction,
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=5),
    )
    diag = result.filter_diagnostics
    assert int(diag["wakeup_exit_ttl_triggered"][2]) == 1
    assert diag["wakeup_exit_reason"][2] == "ttl"
    assert diag["wakeup_position_action"][2] == "exit_ttl"

    trades = pd.DataFrame(
        {
            "trade_id": [1],
            "entry_index": [2],
            "exit_index": [3],
        }
    )
    enriched = attach_trade_filter_diagnostics(trades, diag)
    assert enriched["exit_reason"].iloc[0] == "wakeup_exit_ttl"


def test_mode_d_trade_exit_reason_cycle_limit_close_position_uses_trigger_bar():
    result = apply(
        trend=np.zeros(5, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            max_trades_enabled=True,
            max_trades=1,
            action_mode="close_position",
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=5),
    )
    trades = extract_trades(
        result.positions,
        returns=np.zeros(4, dtype=np.float64),
        execution_prices=np.arange(100.0, 105.0, dtype=np.float64),
        index=pd.date_range("2026-01-01", periods=5, freq="min"),
        commission_rate=0.0,
        trend=np.zeros(5, dtype=np.int64),
    )

    enriched = attach_trade_filter_diagnostics(trades, result.filter_diagnostics)

    assert len(enriched) == 1
    assert enriched["wakeup_cycle_exit_reason"].iloc[0] == "cycle_trade_limit"
    assert enriched["wakeup_position_action"].iloc[0] == "exit_cycle_trade_limit"
    assert enriched["exit_reason"].iloc[0] == "wakeup_exit_cycle_trade_limit"


def test_mode_d_trade_exit_reason_cycle_limit_block_new_entries_keeps_stopping_close():
    trend = np.array([0, 0, 1, 1, -1, -1, -1], dtype=np.int64)
    result = apply(
        trend=trend,
        trade_mode="both",
        trade_filter_config=_cfg(
            ttl_enabled=False,
            max_trades_enabled=True,
            max_trades=1,
            action_mode="block_new_entries",
        ),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1, n=7),
    )
    trades = extract_trades(
        result.positions,
        returns=np.zeros(6, dtype=np.float64),
        execution_prices=np.arange(100.0, 107.0, dtype=np.float64),
        index=pd.date_range("2026-01-01", periods=7, freq="min"),
        commission_rate=0.0,
        trend=trend,
    )

    enriched = attach_trade_filter_diagnostics(trades, result.filter_diagnostics)

    assert len(enriched) == 1
    assert "cycle_trade_limit" in set(result.filter_diagnostics["wakeup_exit_reason"])
    assert enriched["wakeup_cycle_exit_reason"].iloc[0] == "none"
    assert enriched["wakeup_position_action"].iloc[0] == "none"
    assert enriched["exit_reason"].iloc[0] == "filter_stopping_opposite_flip"


def test_mode_d_stopping_close_on_last_bar_uses_pending_open_fallback():
    trades = pd.DataFrame(
        {
            "trade_id": [1],
            "entry_index": [1],
            "exit_index": [4],
        }
    )
    diag = {
        "trade_filter_state": np.array(
            ["OFF", "ST_ACTIVE_FREEZE", "ST_STOPPING", "OFF"], dtype=object
        ),
        "state_at_bar_start": np.array([0, 2, 4, 4], dtype=np.int64),
        "trade_filter_trigger_source": np.array(["none"] * 4, dtype=object),
        "wakeup_exit_reason": np.array(["none"] * 4, dtype=object),
    }

    enriched = attach_trade_filter_diagnostics(trades, diag)
    assert enriched["exit_reason"].iloc[0] == "pending_open_trade_at_end"


def test_mode_d_signal_events_accept_wakeup_trigger_source_smoke():
    df = pd.DataFrame(
        {
            "open": [10.0, 10.5, 11.0, 10.8, 11.2, 11.5],
            "high": [10.4, 10.9, 11.3, 11.0, 11.6, 11.8],
            "low": [9.8, 10.2, 10.7, 10.5, 10.9, 11.2],
            "close": [10.2, 10.7, 10.9, 10.6, 11.4, 11.6],
        },
        index=pd.date_range("2026-01-01", periods=6, freq="D"),
    )
    trend = np.array([1, 1, -1, -1, 1, 1], dtype=np.int8)
    diag = {
        "trade_filter_state": np.array(
            ["OFF", "OFF", "ST_ACTIVE_FREEZE", "ST_ACTIVE_FREEZE", "OFF", "OFF"],
            dtype=object,
        ),
        "filter_allowed_entry": np.array([0, 0, 1, 0, 1, 0], dtype=np.int8),
        "filter_block_reason": np.array(["none"] * 6, dtype=object),
        "trade_filter_trigger_source": np.array(
            ["none", "none", "wakeup_regime", "none", "wakeup_regime", "none"],
            dtype=object,
        ),
    }

    signals = build_signal_events(
        df=df,
        trend=trend,
        atr_period=2,
        trade_mode="revers",
        execution_model=ExecutionModel.OPEN_TO_OPEN,
        filter_diagnostics=diag,
    )

    assert not signals.empty
    assert "filter_trigger_source" in signals.columns
    assert "wakeup_regime" in set(signals["filter_trigger_source"])


def _trade_filter_root_cfg(cfg: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        enabled=True,
        zigzag=SimpleNamespace(
            enabled=True,
            reversal_threshold=0.01,
            local_window=2,
        ),
        lifecycle=cfg.lifecycle,
        diagnostics=SimpleNamespace(
            export_state_columns=True,
            export_trigger_columns=True,
        ),
        wakeup_regime=cfg.wakeup_regime,
    )


def _mode_d_period_result_for_export(
    *,
    action_mode: str,
) -> PeriodResult:
    n = 7
    if action_mode == "block_new_entries":
        trend = np.array([0, 0, 1, 1, -1, -1, -1], dtype=np.int64)
        exit_index = 5
    else:
        trend = np.zeros(n, dtype=np.int64)
        exit_index = 3
    cfg = _cfg(ttl_bars=1, action_mode=action_mode)
    stats = _stats()
    apply_result = apply(
        trend=trend,
        trade_mode="both",
        trade_filter_config=cfg,
        zigzag_global_stats=stats,
        per_bar=_per_bar(t=1, n=n),
    )
    root_cfg = _trade_filter_root_cfg(cfg)
    trades = pd.DataFrame(
        {
            "trade_id": [1],
            "direction": ["long"],
            "entry_time": [pd.Timestamp("2026-01-01 00:02:00")],
            "entry_index": [2],
            "entry_price": [102.0],
            "exit_time": [pd.Timestamp(f"2026-01-01 00:0{exit_index}:00")],
            "exit_index": [exit_index],
            "exit_price": [105.0],
            "bars_held": [exit_index - 2],
            "gross_pnl_pct": [1.0],
            "commission_pct": [0.0],
            "net_pnl_pct": [1.0],
        }
    )
    trades = attach_trade_filter_diagnostics(trades, apply_result.filter_diagnostics)
    bt_result = BacktestResult(
        atr_period=10,
        multiplier=2.0,
        trade_mode="both",
        commission=0.0,
        warmup=0,
        returns=np.zeros(n - 1, dtype=np.float64),
        equity_curve=np.ones(n, dtype=np.float64),
        positions=apply_result.positions,
        trend=trend,
        metrics={
            "sum_pnl_pct": 1.0,
            "sharpe": 0.0,
            "sortino": 0.0,
            "max_drawdown": 0.0,
            "win_rate": 100.0,
            "num_trades": 1,
            "profit_factor": 1.0,
            "avg_trade": 1.0,
            "cagr": 0.0,
        },
        early_exit=False,
        exit_bar=None,
        exit_drawdown=None,
        trades_df=trades,
        n_bars_original=n,
        period_label="100%",
        effective_warmup=0,
        filter_diagnostics=apply_result.filter_diagnostics,
    )
    summary = _build_filter_diagnostics_summary(
        SimpleNamespace(
            filter_diagnostics=apply_result.filter_diagnostics,
            trend=trend,
            trade_mode="both",
            positions=apply_result.positions,
            trades_df=trades,
        ),
        root_cfg,
        stats,
        global_offset=0,
    )
    return PeriodResult(
        period_label="100%",
        n_bars=n,
        result=bt_result,
        filter_diagnostics=apply_result.filter_diagnostics,
        filter_diagnostics_summary=summary,
    )


@pytest.mark.parametrize(
    ("action_mode", "expected_exit_reason"),
    [
        ("block_new_entries", "filter_stopping_opposite_flip"),
        ("close_position", "wakeup_exit_ttl"),
    ],
)
def test_mode_d_full_tester_xlsx_export_for_both_action_modes(
    tmp_path,
    action_mode,
    expected_exit_reason,
):
    period = _mode_d_period_result_for_export(action_mode=action_mode)
    root_cfg = SimpleNamespace(
        enabled=True,
        zigzag=SimpleNamespace(enabled=True),
        diagnostics=SimpleNamespace(
            export_state_columns=True,
            export_trigger_columns=True,
        ),
    )
    df = pd.DataFrame(
        {
            "open": np.arange(100.0, 107.0),
            "high": np.arange(101.0, 108.0),
            "low": np.arange(99.0, 106.0),
            "close": np.arange(100.5, 107.5),
        },
        index=pd.date_range("2026-01-01", periods=7, freq="min"),
    )
    out_path = tmp_path / f"wakeup_{action_mode}.xlsx"

    actual_path = export_tester_results(
        [period],
        str(out_path),
        trade_filter_config=root_cfg,
        df=df,
        export_diagnostics=True,
        export_signals=False,
        export_false_start=False,
        export_cycle=False,
        add_timestamp=False,
    )

    with pd.ExcelFile(actual_path) as xlsx:
        assert {
            "FilterDiagnostics_100",
            "ZigZag_Trigger_Events",
            "filters_summary",
            "Trades_100",
        }.issubset(set(xlsx.sheet_names))
        filter_diag = xlsx.parse("FilterDiagnostics_100")
        trigger_events = xlsx.parse("ZigZag_Trigger_Events")
        trades = xlsx.parse("Trades_100")
        summary_sheet = xlsx.parse("filters_summary", header=None)

    assert "Wakeup Exit Reason" in set(filter_diag.columns)
    assert "Exit Reason" not in set(trades.columns)
    assert "Trade Close Reason" in set(trades.columns)
    assert "Wakeup Cycle Exit Reason" in set(trades.columns)
    assert "Wakeup Position Action" in set(trades.columns)
    trigger_row = trigger_events.iloc[0]
    assert trigger_row["Trigger Source"] == "wakeup_regime"
    assert trigger_row["Linked Trade ID"] == 1
    assert trigger_row["Threshold Used"] == pytest.approx(0.10)
    assert trigger_row["Quantile Used"] == pytest.approx(0.65)
    assert trades["Trade Close Reason"].iloc[0] == expected_exit_reason
    if action_mode == "close_position":
        assert trades["Wakeup Cycle Exit Reason"].iloc[0] == "ttl"
        assert trades["Wakeup Position Action"].iloc[0] == "exit_ttl"
    else:
        assert trades["Wakeup Cycle Exit Reason"].iloc[0] == "none"
        assert trades["Wakeup Position Action"].iloc[0] == "none"
    period_header_idx = int(
        summary_sheet.index[summary_sheet.iloc[:, 0] == "Period"][0]
    )
    summary_params = summary_sheet.iloc[1:period_header_idx - 1, :2]
    params = dict(zip(summary_params.iloc[:, 0], summary_params.iloc[:, 1]))
    summary_period = pd.DataFrame(
        [summary_sheet.iloc[period_header_idx + 1].to_list()],
        columns=summary_sheet.iloc[period_header_idx].to_list(),
    )
    assert params["Wakeup Exit Action Mode"] == action_mode
    assert summary_period["Wakeup Starts"].iloc[0] == 1


def test_mode_d_enters_active_freeze_without_st_flip_open_to_open():
    result = apply(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1),
    )

    diag = result.filter_diagnostics
    assert result.positions.tolist() == [0, 0, 1, 1, 1, 1]
    assert diag["trade_filter_trigger_source"][1] == "wakeup_regime"
    assert int(diag["filter_allowed_entry"][1]) == 1
    assert set(diag["exit_off_mode"]) == {"exit C"}
    assert set(diag["exit_off_zz_leg_count"]) == {-1}
    forbidden = {
        "WAIT_FIRST_ST_FLIP",
        "ST_ACTIVE_MONITORING",
        "ST_COUNTING_ZZ_LEGS",
    }
    assert forbidden.isdisjoint(set(diag["trade_filter_state"]))


def test_mode_d_lock_start_bar_st_flip_does_not_override_start_direction():
    result = apply(
        trend=np.array([1, -1, -1, -1, -1, -1], dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(lock_cycle_direction=True),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=1),
    )

    diag = result.filter_diagnostics
    assert diag["trade_filter_trigger_source"][1] == "wakeup_regime"
    assert int(diag["wakeup_active_direction"][1]) == 1
    assert diag["wakeup_position_action"][1] == "none"


@pytest.mark.parametrize(
    ("trade_mode", "direction"),
    [
        ("long", -1),
        ("short", 1),
    ],
)
def test_mode_d_rejects_direction_disallowed_by_trade_mode(trade_mode, direction):
    result = apply(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode=trade_mode,
        trade_filter_config=_cfg(),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(direction=direction),
    )

    assert np.all(result.positions == 0)
    assert set(result.filter_diagnostics["trade_filter_trigger_source"]) == {"none"}


def test_mode_d_requires_all_enabled_entry_components():
    result = apply(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(height=0.09),
    )

    assert np.all(result.positions == 0)
    assert np.all(
        result.filter_diagnostics["trade_filter_state_code"]
        == int(ZigZagFSMState.OFF)
    )


def test_mode_d_atr_and_volume_components_can_open_entry():
    n = 6
    high, low, close = _valid_ohlc(n)
    result = apply(
        trend=np.zeros(n, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(atr_enabled=True, volume_enabled=True),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=3, n=n),
        high=high,
        low=low,
        close=close,
        volume=np.full(n, 100.0, dtype=np.float64),
    )

    expected_ratio = _compute_wakeup_atr_ratio(
        high, low, close, short_window=2, long_window=4
    )
    diag = result.filter_diagnostics
    assert result.positions.tolist() == [0, 0, 0, 0, 1, 1]
    assert diag["trade_filter_trigger_source"][3] == "wakeup_regime"
    np.testing.assert_allclose(diag["wakeup_entry_atr_ratio"], expected_ratio)
    assert np.isnan(diag["wakeup_entry_atr_ratio"][:3]).all()
    assert int(diag["wakeup_entry_atr_ok"][2]) == 0
    assert int(diag["wakeup_entry_atr_ok"][3]) == 1


@pytest.mark.parametrize("missing", ["high", "low", "close"])
def test_mode_d_atr_component_requires_ohlc_arrays(missing):
    high, low, close = _valid_ohlc()
    kwargs = {
        "high": high,
        "low": low,
        "close": close,
    }
    kwargs[missing] = None

    with pytest.raises(ConfigError, match="OHLC arrays"):
        apply(
            trend=np.zeros(6, dtype=np.int64),
            trade_mode="both",
            trade_filter_config=_cfg(atr_enabled=True),
            zigzag_global_stats=_stats(),
            per_bar=_per_bar(t=3),
            **kwargs,
        )


@pytest.mark.parametrize(
    "updates",
    [
        {"high": np.ones(5, dtype=np.float64)},
        {"low": np.ones((1, 6), dtype=np.float64)},
        {"close": np.array([10.0, 11.0, np.nan, 13.0, 14.0, 15.0])},
        {"high": np.array([11.0, 12.0, np.inf, 14.0, 15.0, 16.0])},
        {"high": np.array(["bad", "12", "13", "14", "15", "16"])},
        {"low": np.array(["9", "10", "bad", "12", "13", "14"])},
        {"close": np.array(["10", "11", "12", "bad", "14", "15"])},
        {
            "high": np.array([11.0, 12.0, 13.0, 9.0, 15.0, 16.0]),
            "low": np.array([9.0, 10.0, 11.0, 12.0, 13.0, 14.0]),
        },
    ],
)
def test_mode_d_atr_component_rejects_invalid_ohlc_arrays(updates):
    high, low, close = _valid_ohlc()
    kwargs = {
        "high": high,
        "low": low,
        "close": close,
    }
    kwargs.update(updates)

    with pytest.raises(ConfigError, match="OHLC arrays"):
        apply(
            trend=np.zeros(6, dtype=np.int64),
            trade_mode="both",
            trade_filter_config=_cfg(atr_enabled=True),
            zigzag_global_stats=_stats(),
            per_bar=_per_bar(t=3),
            **kwargs,
        )
