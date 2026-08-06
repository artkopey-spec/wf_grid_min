"""STEP-1 characterization for paths that reversal confirmation must preserve."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import load_workbook

from supertrend_optimizer.core._reset_events import (
    TimeFilterRuntime,
    TimeWindowSchedule,
)
from supertrend_optimizer.core.backtest import generate_positions
from supertrend_optimizer.core.time_only_filter import apply
from supertrend_optimizer.core.trades import extract_trades
from supertrend_optimizer.io.excel_tester import (
    _CANONICAL_TRADE_DISPLAY_COLS,
    export_tester_results,
)
from supertrend_optimizer.testing.runner import run_all_periods


GOLDEN = (
    Path(__file__).with_name("reversal_confirmation_snapshots")
    / "prechange_contract.json"
)


def _golden() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def _trend() -> np.ndarray:
    return np.asarray(_golden()["trend"], dtype=np.int8)


def _time_runtime() -> TimeFilterRuntime:
    return TimeFilterRuntime(
        reference_length=8,
        schedule=TimeWindowSchedule(((9 * 60, 12 * 60),)),
        in_window=np.asarray(
            [False, True, True, True, True, True, False, False],
            dtype=bool,
        ),
        reset_event=np.asarray(
            [False, False, False, True, False, True, False, False],
            dtype=bool,
        ),
    )


def test_prechange_position_contract_all_trade_modes() -> None:
    golden = _golden()
    observed: dict[str, np.ndarray] = {}
    for mode in ("revers", "both", "long", "short"):
        positions = generate_positions(_trend(), mode)
        observed[mode] = positions
        assert positions.dtype == np.dtype(golden["position_dtype"])
        assert positions.shape == _trend().shape
        np.testing.assert_array_equal(positions, golden["positions"][mode])
    np.testing.assert_array_equal(observed["both"], observed["revers"])


def test_prechange_time_only_positions_and_metadata() -> None:
    golden = _golden()
    result = apply(
        trend=_trend(),
        trade_mode="revers",
        time_filter_runtime=_time_runtime(),
        collect_filter_diagnostics=False,
    )
    np.testing.assert_array_equal(
        result.positions,
        golden["time_only_positions"],
    )
    assert result.positions.dtype == np.int8
    assert result.filter_diagnostics is None
    for key, expected in golden["time_only_metadata"].items():
        np.testing.assert_array_equal(result.execution_metadata[key], expected)


def test_prechange_normalized_trade_schema() -> None:
    golden = _golden()
    positions = generate_positions(_trend(), "revers")
    prices = np.arange(100.0, 108.0)
    returns = np.zeros(7, dtype=float)
    trades = extract_trades(
        positions=positions,
        returns=returns,
        execution_prices=prices,
        index=np.arange(8),
        commission_rate=0.0,
        trend=_trend(),
    )
    assert list(trades.columns) == golden["inactive_trade_columns"]


def test_prechange_inactive_excel_schema_is_exported_and_pinned(tmp_path) -> None:
    n = 48
    x = np.arange(n, dtype=float)
    close = 100.0 + np.sin(x / 3.0)
    frame = pd.DataFrame(
        {
            "open": np.r_[close[0], close[:-1]],
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
        },
        index=pd.date_range("2026-01-01", periods=n, freq="min"),
    )
    results = run_all_periods(
        frame,
        atr_period=2,
        multiplier=1.0,
        trade_mode="revers",
        commission=0.0,
        warmup_period=0,
        min_trades_required=0,
        include_period_splits=False,
    )
    output = export_tester_results(
        results,
        str(tmp_path / "inactive_contract.xlsx"),
        trade_filter_config=None,
        df=frame,
        export_diagnostics=False,
        export_signals=False,
        export_false_start=False,
        export_cycle=False,
        export_trades=True,
        add_timestamp=False,
    )
    workbook = load_workbook(output, read_only=True, data_only=True)
    try:
        assert workbook.sheetnames == [
            "Tester_Config",
            "Summary",
            "Metrics_100",
            "Trades_100",
        ]
        assert workbook.sheetnames[:2] == _golden()["inactive_excel_prefix"]
        trade_headers = [
            cell.value for cell in next(workbook["Trades_100"].iter_rows())
        ]
        assert trade_headers == _CANONICAL_TRADE_DISPLAY_COLS
    finally:
        workbook.close()
