from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.core.zigzag_st_filter import (
    ZigZagFSMState,
    ZigZagGlobalStats,
    ZigZagPerBar,
    attach_trade_filter_diagnostics as attach_trade_filter_diagnostics_public,
    apply,
)
from supertrend_optimizer.core.filter_trade_diagnostics import (
    attach_trade_filter_diagnostics,
)
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
    action_mode: str = "block_new_entries",
) -> SimpleNamespace:
    return SimpleNamespace(
        lifecycle=SimpleNamespace(
            freeze_confirmed_legs=0,
            exit_off_mode="exit C",
            exit_b_immediate_off=False,
        ),
        wakeup_regime=SimpleNamespace(
            enabled=True,
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
            ),
            exit=SimpleNamespace(
                ttl=SimpleNamespace(enabled=ttl_enabled, bars=ttl_bars),
                no_fresh_candidate=SimpleNamespace(
                    enabled=no_fresh_enabled,
                    quantile=0.60,
                    max_age_bars=2,
                    timeout_bars=no_fresh_timeout_bars,
                ),
                action=SimpleNamespace(mode=action_mode),
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


def test_mode_d_active_freeze_st_flip_reverses_without_ending_cycle():
    result = apply(
        trend=np.array([1, 1, 1, 1, 1, -1, -1], dtype=np.int64),
        trade_mode="both",
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
        "wakeup_exit_ttl_triggered": np.int8,
        "wakeup_exit_no_fresh_candidate_triggered": np.int8,
        "wakeup_exit_close_triggered": np.int8,
        "wakeup_exit_action_mode": object,
        "wakeup_exit_reason": object,
        "wakeup_position_action": object,
        "wakeup_active_direction": np.int8,
    }
    assert set(wakeup_dtypes).issubset(diag)
    for key, dtype in wakeup_dtypes.items():
        arr = diag[key]
        assert len(arr) == len(result.positions), key
        assert arr.dtype == dtype, key

    assert set(diag["wakeup_exit_action_mode"]) == {"block_new_entries"}
    assert set(diag["wakeup_exit_reason"]).issubset(
        {"none", "ttl", "no_fresh_candidate", "reset"}
    )
    assert set(diag["wakeup_position_action"]).issubset(
        {
            "none",
            "reverse_on_st_flip",
            "flat_on_disallowed_st_flip",
            "restore_allowed_position_on_st_flip",
            "exit_ttl",
            "exit_no_fresh_candidate",
            "exit_reset",
        }
    )
    assert set(diag["wakeup_active_direction"]).issubset({-1, 0, 1})
    assert int(diag["wakeup_entry_all_ok"][1]) == 1
    assert diag["wakeup_entry_candidate_height_value"][1] == pytest.approx(0.12)
    assert diag["wakeup_entry_candidate_height_threshold"][1] == pytest.approx(0.10)
    assert int(diag["wakeup_entry_candidate_age_bars"][1]) == 3
    assert int(diag["wakeup_entry_candidate_leg_direction"][1]) == 1


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
    assert summary["wakeup_exit_close_count"] == 1
    assert summary["wakeup_reverse_on_st_flip_count"] == 0
    assert summary["wakeup_flat_on_disallowed_st_flip_count"] == 0
    assert summary["wakeup_restore_allowed_position_on_st_flip_count"] == 0
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
        "wakeup_exit_ttl_triggered",
        "wakeup_exit_no_fresh_candidate_triggered",
        "wakeup_exit_close_triggered",
        "wakeup_exit_action_mode",
        "wakeup_exit_reason",
        "wakeup_position_action",
        "wakeup_active_direction",
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

    summary = {
        "zigzag_mode": "D",
        "wakeup_enabled": True,
        "wakeup_exit_action_mode": "block_new_entries",
        "wakeup_starts_count": 2,
        "wakeup_entry_attempts_count": 3,
        "wakeup_exit_ttl_count": 1,
        "wakeup_exit_no_fresh_candidate_count": 1,
        "wakeup_exit_close_count": 0,
        "wakeup_exit_reset_count": 1,
        "wakeup_exit_opposite_st_flip_count": 1,
        "wakeup_reverse_on_st_flip_count": 2,
        "wakeup_flat_on_disallowed_st_flip_count": 3,
        "wakeup_restore_allowed_position_on_st_flip_count": 4,
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
    assert row["Wakeup Exit No Fresh Candidate"] == 1
    assert row["Wakeup Exit Close"] == 0
    assert row["Wakeup Exit Reset"] == 1
    assert row["Wakeup Exit Opposite ST Flip"] == 1
    assert row["Wakeup Reverse On ST Flip"] == 2
    assert row["Wakeup Flat On Disallowed ST Flip"] == 3
    assert row["Wakeup Restore Allowed Position On ST Flip"] == 4
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
        ("restore_allowed_position_on_st_flip", "st_flip"),
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


def test_mode_d_trade_exit_reason_exit_c_beats_same_bar_st_flip_action():
    result = apply(
        trend=np.array([1, 1, -1, -1, -1], dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(ttl_bars=1, action_mode="close_position"),
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


def test_mode_d_rejects_direction_disallowed_by_trade_mode():
    result = apply(
        trend=np.zeros(6, dtype=np.int64),
        trade_mode="long",
        trade_filter_config=_cfg(),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(direction=-1),
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
    close = np.arange(10.0, 16.0, dtype=np.float64)
    result = apply(
        trend=np.zeros(n, dtype=np.int64),
        trade_mode="both",
        trade_filter_config=_cfg(atr_enabled=True, volume_enabled=True),
        zigzag_global_stats=_stats(),
        per_bar=_per_bar(t=3, n=n),
        close=close,
        volume=np.full(n, 100.0, dtype=np.float64),
    )

    assert result.positions.tolist() == [0, 0, 0, 0, 1, 1]
    assert result.filter_diagnostics["trade_filter_trigger_source"][3] == (
        "wakeup_regime"
    )


def test_mode_d_atr_component_requires_close_array():
    with pytest.raises(ConfigError, match="requires close"):
        apply(
            trend=np.zeros(6, dtype=np.int64),
            trade_mode="both",
            trade_filter_config=_cfg(atr_enabled=True),
            zigzag_global_stats=_stats(),
            per_bar=_per_bar(t=3),
        )
