from __future__ import annotations

import numpy as np
import pandas as pd

from supertrend_optimizer.core._reset_events import (
    TimeWindowSchedule,
    build_time_filter_runtime,
)
from supertrend_optimizer.core.trade_filter_config import (
    build_trade_filter_config_from_raw,
    collect_raw_user_keys,
    resolve_time_filter_in_place,
    resolve_volume_enabled_in_place,
    resolve_zigzag_enabled_in_place,
    validate_trade_filter,
)
from supertrend_optimizer.engine.result import BacktestResult
from supertrend_optimizer.utils.time_utils import WFWindowSlice
from wf_grid.config.schema import (
    BacktestConfig,
    DataConfig,
    ExecutionConfig,
    ExportConfig,
    GridConfig,
    OptimizationConfig,
    ValidationConfig,
    WalkForwardConfig,
)
from wf_grid.grid.enumeration import GridPoint
from wf_grid.pipeline.orchestrator import (
    _execute_wf_grid_parallel,
    _execute_wf_grid_sequential,
)
from wf_grid.wf import _mp_helpers, step_executor
from wf_grid.wf.step_executor import execute_oos_step


def _data(n: int = 80) -> pd.DataFrame:
    x = np.linspace(0.0, 8.0 * np.pi, n)
    close = 100.0 + 3.0 * np.sin(x)
    open_prices = np.r_[close[0], close[:-1]]
    return pd.DataFrame(
        {
            "open": open_prices,
            "high": np.maximum(open_prices, close) + 1.0,
            "low": np.minimum(open_prices, close) - 1.0,
            "close": close,
        },
        index=pd.date_range("2026-03-06 09:00", periods=n, freq="min"),
    )


def _trade_filter():
    raw = {
        "enabled": True,
        "zigzag": {"enabled": False},
        "volume": {"enabled": False},
        "time_filter": {
            "enabled": True,
            "windows": ["09:00-09:20", "09:30-09:50"],
        },
    }
    keys = collect_raw_user_keys({"trade_filter": raw})
    config = build_trade_filter_config_from_raw(raw)
    resolve_zigzag_enabled_in_place(config, keys)
    resolve_volume_enabled_in_place(config, keys)
    errors: list[str] = []
    validate_trade_filter(config, errors, keys, caller_pipeline="wf_grid")
    assert errors == []
    resolve_time_filter_in_place(config, keys)
    return config


def _config() -> GridConfig:
    return GridConfig(
        data=DataConfig(file_path=""),
        optimization=OptimizationConfig(
            atr_period_range=[5, 5],
            multiplier_range=[1.8, 1.8],
            multiplier_step=1.0,
            trade_mode="revers",
        ),
        backtest=BacktestConfig(commission=0.0, min_trades_required=0),
        validation=ValidationConfig(
            warmup_period=0,
            walk_forward=WalkForwardConfig(
                train_size="30m",
                test_size="10m",
                step_size="10m",
                min_train_bars=5,
                min_test_bars=5,
            ),
        ),
        execution=ExecutionConfig(parallel_enabled=False),
        export=ExportConfig(retain_per_bar_filter_diagnostics=True),
        trade_filter=_trade_filter(),
        resolved_periods_per_year=365.0,
    )


def _runtime(data: pd.DataFrame):
    return build_time_filter_runtime(
        data.index,
        len(data),
        enabled=True,
        schedule=TimeWindowSchedule(
            tuple(_trade_filter().time_filter._windows_minutes)
        ),
    )


def _grid_points() -> list[GridPoint]:
    return [
        GridPoint(5, 1.8, "revers", "atr5_m1.80_revers"),
        GridPoint(5, 2.0, "revers", "atr5_m2.00_revers"),
    ]


def _slices() -> list[WFWindowSlice]:
    return [
        WFWindowSlice(0, 0, 30, 30, 45),
        WFWindowSlice(1, 10, 40, 45, 60),
    ]


def test_oos_uses_runtime_slice_and_clears_boundary_reset(monkeypatch) -> None:
    data = _data()
    runtime = _runtime(data)
    captured = {}

    def fake_run_single_backtest(**kwargs):
        sliced = kwargs["time_filter_runtime"]
        captured["length"] = sliced.reference_length
        captured["first_reset"] = bool(sliced.reset_event[0])
        n = len(kwargs["close"])
        return BacktestResult(
            atr_period=kwargs["atr_period"],
            multiplier=kwargs["multiplier"],
            trade_mode=kwargs["trade_mode"],
            commission=kwargs["commission"],
            warmup=0,
            returns=np.zeros(n - 1),
            equity_curve=np.ones(n),
            positions=np.zeros(n, dtype=np.int8),
            trend=np.ones(n, dtype=np.int8),
            metrics={"num_trades": 0, "sum_pnl_pct": 0.0},
            early_exit=False,
            exit_bar=None,
            exit_drawdown=None,
            trades_df=pd.DataFrame(),
            n_bars_original=n,
        )

    monkeypatch.setattr(step_executor, "run_single_backtest", fake_run_single_backtest)
    execute_oos_step(
        grid_point=_grid_points()[0],
        wf_slice=_slices()[0],
        full_open=data["open"].to_numpy(),
        full_high=data["high"].to_numpy(),
        full_low=data["low"].to_numpy(),
        full_close=data["close"].to_numpy(),
        full_index=data.index,
        config=_config(),
        prepend_bars_requested=5,
        time_filter_runtime=runtime,
    )
    assert captured == {"length": 20, "first_reset": False}


def test_worker_reuses_initializer_runtime_for_multiple_tasks(monkeypatch) -> None:
    data = _data()
    runtime = _runtime(data)
    seen: list[int] = []

    def fake_run(**kwargs):
        seen.append(id(kwargs["time_filter_runtime"]))
        return []

    monkeypatch.setattr("wf_grid.wf.runner.run_wf_for_grid_point", fake_run)
    monkeypatch.setattr("wf_grid.wf.runner.run_wf_train_for_grid_point", fake_run)
    _mp_helpers._init_worker(
        project_root="",
        donor_path="",
        data_dict=_mp_helpers.pack_data(data),
        wf_slices=_slices(),
        config=_config(),
        prepend_bars=0,
        zigzag_global_stats=None,
        volume_runtime=None,
        time_filter_runtime=runtime,
    )
    _mp_helpers._run_grid_point_both(_grid_points()[0])
    _mp_helpers._run_grid_point_both(_grid_points()[1])
    assert seen == [id(runtime)] * 4


def test_time_only_sequential_parallel_equality() -> None:
    data = _data()
    config = _config()
    runtime = _runtime(data)
    args = dict(
        grid_points=_grid_points(),
        wf_slices=_slices(),
        data=data,
        config=config,
        prepend_bars=0,
        zigzag_global_stats=None,
        volume_runtime=None,
        time_filter_runtime=runtime,
    )
    sequential_oos, sequential_train = _execute_wf_grid_sequential(**args)
    parallel_oos, parallel_train = _execute_wf_grid_parallel(
        **args, n_workers=2
    )
    for sequential, parallel in (
        (sequential_oos, parallel_oos),
        (sequential_train, parallel_train),
    ):
        assert sequential.keys() == parallel.keys()
        for grid_point_id in sequential:
            assert len(sequential[grid_point_id]) == len(parallel[grid_point_id])
            for left, right in zip(
                sequential[grid_point_id], parallel[grid_point_id]
            ):
                assert left.error_message == right.error_message
                assert left.metrics == right.metrics
                assert (
                    left.filter_diagnostics_summary
                    == right.filter_diagnostics_summary
                )
                if left.filter_diagnostics_oos is not None:
                    for key, values in left.filter_diagnostics_oos.items():
                        np.testing.assert_array_equal(
                            values, right.filter_diagnostics_oos[key]
                        )
