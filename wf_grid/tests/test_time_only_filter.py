from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from supertrend_optimizer.core._reset_events import (
    TimeFilterRuntime,
    TimeWindowSchedule,
)
from supertrend_optimizer.core.filter_trade_diagnostics import (
    attach_trade_filter_diagnostics,
)
from supertrend_optimizer.core.time_only_filter import apply
from supertrend_optimizer.core.trade_filter_decision import (
    decide_trade_filter_entry,
)
from supertrend_optimizer.engine.run import run_single_backtest


SCHEDULE = TimeWindowSchedule(((9 * 60, 12 * 60), (16 * 60, 18 * 60)))


def _runtime(in_window, reset_event) -> TimeFilterRuntime:
    return TimeFilterRuntime(
        reference_length=len(in_window),
        schedule=SCHEDULE,
        in_window=np.asarray(in_window, dtype=bool),
        reset_event=np.asarray(reset_event, dtype=bool),
    )


def test_dense_window_open_enters_with_one_bar_lag() -> None:
    result = apply(
        trend=np.array([1, 1, 1, 1]),
        trade_mode="both",
        time_filter_runtime=_runtime(
            [False, True, True, True],
            [False, False, False, False],
        ),
    )
    np.testing.assert_array_equal(result.positions, [0, 0, 1, 1])
    assert result.filter_diagnostics["filter_allowed_entry"][1] == 1


def test_gap_reset_reenters_without_new_trend_flip() -> None:
    result = apply(
        trend=np.ones(4, dtype=np.int8),
        trade_mode="both",
        time_filter_runtime=_runtime(
            [True, True, True, True],
            [False, True, False, False],
        ),
    )
    np.testing.assert_array_equal(result.positions, [0, 1, 0, 1])
    diagnostics = result.filter_diagnostics
    assert diagnostics["filter_block_reason"][1] == "time_filter_reset"
    assert diagnostics["filter_allowed_entry"][2] == 1
    assert diagnostics["time_filter_forced_exit"][1] == 1


def test_outside_attempt_is_edge_based_not_repeated_passively() -> None:
    result = apply(
        trend=np.array([1, 1, -1, -1]),
        trade_mode="both",
        time_filter_runtime=_runtime(
            [False, False, False, False],
            [False, False, False, False],
        ),
    )
    np.testing.assert_array_equal(result.positions, [0, 0, 0, 0])
    np.testing.assert_array_equal(
        result.filter_diagnostics["filter_block_reason"],
        [
            "time_filter_out_of_window",
            "none",
            "time_filter_out_of_window",
            "none",
        ],
    )


def test_last_bar_reset_is_diagnostic_but_not_executed_exit() -> None:
    result = apply(
        trend=np.ones(3, dtype=np.int8),
        trade_mode="both",
        time_filter_runtime=_runtime(
            [True, True, False],
            [False, False, True],
        ),
    )
    np.testing.assert_array_equal(result.positions, [0, 1, 1])
    assert result.filter_diagnostics["time_filter_reset_event"][2] == 1
    assert result.filter_diagnostics["time_filter_forced_exit"][2] == 0


def test_trade_mode_narrowing_uses_baseline_target() -> None:
    runtime = _runtime([True] * 4, [False] * 4)
    long_result = apply(
        trend=np.array([-1, 1, -1, 1]),
        trade_mode="long",
        time_filter_runtime=runtime,
    )
    short_result = apply(
        trend=np.array([-1, 1, -1, 1]),
        trade_mode="short",
        time_filter_runtime=runtime,
    )
    np.testing.assert_array_equal(long_result.positions, [0, 0, 1, 0])
    np.testing.assert_array_equal(short_result.positions, [0, -1, 0, -1])


def test_collect_false_keeps_compact_exit_attribution_metadata() -> None:
    result = apply(
        trend=np.ones(4, dtype=np.int8),
        trade_mode="both",
        time_filter_runtime=_runtime(
            [True, True, False, False],
            [False, False, True, False],
        ),
        collect_filter_diagnostics=False,
    )
    assert result.filter_diagnostics is None
    trades = pd.DataFrame(
        [{"entry_index": 1, "exit_index": 3}]
    )
    enriched = attach_trade_filter_diagnostics(
        trades, result.execution_metadata
    )
    assert enriched["exit_reason"].iloc[0] == "filter_time_reset"
    assert "entry_filter_state" not in enriched
    assert "entry_trigger_source" not in enriched


def test_shared_gate_priority() -> None:
    daily = decide_trade_filter_entry(
        entry_attempt_event=True,
        time_filter_in_window=False,
        combined_reset_event=True,
        daily_reset_event=True,
        time_filter_reset_event=True,
    )
    assert daily.reason == "daily_reset"

    outside = decide_trade_filter_entry(
        entry_attempt_event=True,
        time_filter_in_window=False,
        combined_reset_event=False,
        daily_reset_event=False,
        time_filter_reset_event=False,
    )
    assert outside.entry_blocked_outside is True
    assert outside.reason == "time_filter_out_of_window"


def test_trade_exit_attribution_prefers_resets_over_wakeup_reset() -> None:
    trades = pd.DataFrame({"entry_index": [1], "exit_index": [3]})
    diagnostics = {
        "daily_reset_event": np.array([0, 0, 1, 0], dtype=np.int8),
        "time_filter_reset_event": np.array([0, 0, 1, 0], dtype=np.int8),
        "wakeup_exit_reason": np.array(
            ["none", "none", "reset", "none"], dtype=object
        ),
    }
    enriched = attach_trade_filter_diagnostics(trades, diagnostics)
    assert enriched["exit_reason"].iloc[0] == "filter_daily_reset"

    diagnostics["daily_reset_event"] = np.zeros(4, dtype=np.int8)
    enriched = attach_trade_filter_diagnostics(trades, diagnostics)
    assert enriched["exit_reason"].iloc[0] == "filter_time_reset"


def test_direct_backtest_exit_attribution_without_heavy_diagnostics() -> None:
    n = 80
    x = np.linspace(0.0, 8.0 * np.pi, n)
    close = 100.0 + 3.0 * np.sin(x)
    open_prices = np.r_[close[0], close[:-1]]
    index = pd.date_range("2026-03-06 09:00", periods=n, freq="min")
    trade_filter = SimpleNamespace(
        enabled=True,
        zigzag=SimpleNamespace(enabled=False),
        volume=SimpleNamespace(enabled=False),
        time_filter=SimpleNamespace(
            enabled=True,
            _windows_minutes=((9 * 60, 9 * 60 + 20), (9 * 60 + 30, 9 * 60 + 50)),
        ),
    )
    result = run_single_backtest(
        open_prices=open_prices,
        high=np.maximum(open_prices, close) + 1.0,
        low=np.minimum(open_prices, close) - 1.0,
        close=close,
        index=index,
        atr_period=5,
        multiplier=1.8,
        trade_mode="revers",
        commission=0.0,
        warmup_period=0,
        periods_per_year=365.0,
        min_trades_required=0,
        trade_filter_config=trade_filter,
        collect_filter_diagnostics=False,
    )
    assert result.filter_diagnostics is None
    assert len(result.trades_df) > 0
    assert "exit_reason" in result.trades_df
    assert "entry_filter_state" not in result.trades_df
    assert "entry_trigger_source" not in result.trades_df
