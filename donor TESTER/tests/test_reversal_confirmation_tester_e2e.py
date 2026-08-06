from __future__ import annotations

import numpy as np
import pandas as pd
from openpyxl import load_workbook

from supertrend_optimizer.cli.tester import load_tester_config
from supertrend_optimizer.io.excel_tester import (
    SIGNALS_DISPLAY_NAMES,
    export_tester_results,
)
from supertrend_optimizer.testing.reversal_confirmation_export import (
    EXECUTION_SIGNAL_COLUMNS,
    REVERSAL_EVENT_COLUMNS,
)
from supertrend_optimizer.testing.runner import run_all_periods
from supertrend_optimizer.testing.signal_events import build_signal_events
from supertrend_optimizer.utils.enums import ExecutionModel


def _config():
    raw = {
        "trade_filter": {
            "enabled": True,
            "zigzag": {"enabled": False},
            "volume": {"enabled": False},
            "time_filter": {"enabled": False},
            "reversal_confirmation": {
                "enabled": True,
                "confirm_bars": 1,
            },
        }
    }
    return load_tester_config("memory.yml", loaded_raw=raw)["trade_filter"]


def test_legacy_periods_use_fresh_event_id_namespace() -> None:
    n = 120
    x = np.arange(n)
    close = 100.0 + 4.0 * np.sin(x / 4.0)
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
        trade_filter_config=_config(),
    )
    assert len(results) == 5
    for period in results:
        events = period.reversal_confirmation_events
        assert events is not None
        if events:
            assert events[0].event_id == 0
        assert period.reversal_confirmation_summary.active_pending == 0


def test_active_tester_export_preserves_signals_and_adds_execution_sheets(
    tmp_path,
) -> None:
    n = 120
    x = np.arange(n)
    close = 100.0 + 4.0 * np.sin(x / 4.0)
    frame = pd.DataFrame(
        {
            "open": np.r_[close[0], close[:-1]],
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1000.0 + x,
        },
        index=pd.date_range("2026-01-01", periods=n, freq="min"),
    )
    config = _config()
    results = run_all_periods(
        frame,
        atr_period=2,
        multiplier=1.0,
        trade_mode="revers",
        commission=0.0,
        warmup_period=0,
        min_trades_required=0,
        trade_filter_config=config,
    )
    signals = build_signal_events(
        df=frame,
        trend=results[0].result.trend,
        atr_period=2,
        trade_mode="revers",
        execution_model=ExecutionModel.OPEN_TO_OPEN,
        filter_diagnostics=results[0].filter_diagnostics,
    )
    output = export_tester_results(
        results,
        str(tmp_path / "reversal_active.xlsx"),
        signals_df=signals,
        trade_filter_config=config,
        df=frame,
        export_diagnostics=False,
        export_signals=True,
        export_false_start=True,
        export_cycle=False,
        export_trades=True,
        add_timestamp=False,
    )

    workbook = load_workbook(output, read_only=True, data_only=True)
    try:
        assert "Signals" in workbook.sheetnames
        assert "ExecutionSignals" in workbook.sheetnames
        assert "ReversalConfirmationEvents" in workbook.sheetnames
        assert "false start" in workbook.sheetnames

        signal_sheet = workbook["Signals"]
        signal_headers = [cell.value for cell in next(signal_sheet.iter_rows())]
        assert signal_headers == [
            SIGNALS_DISPLAY_NAMES.get(column, column)
            for column in signals.columns
        ]
        assert signal_sheet.max_row == len(signals) + 1

        execution_sheet = workbook["ExecutionSignals"]
        assert [cell.value for cell in next(execution_sheet.iter_rows())] == (
            EXECUTION_SIGNAL_COLUMNS
        )
        execution_rows = list(
            execution_sheet.iter_rows(min_row=2, values_only=True)
        )
        by_bar: dict[int, list[str]] = {}
        event_type_idx = EXECUTION_SIGNAL_COLUMNS.index("event_type")
        execution_bar_idx = EXECUTION_SIGNAL_COLUMNS.index("execution_bar")
        for row in execution_rows:
            by_bar.setdefault(
                int(row[execution_bar_idx]), []
            ).append(str(row[event_type_idx]))
        reversal_transitions = [
            event_types
            for event_types in by_bar.values()
            if "close" in event_types and "open" in event_types
        ]
        assert reversal_transitions
        assert all(
            event_types.index("close") < event_types.index("open")
            for event_types in reversal_transitions
        )

        event_sheet = workbook["ReversalConfirmationEvents"]
        assert [cell.value for cell in next(event_sheet.iter_rows())] == (
            REVERSAL_EVENT_COLUMNS
        )

        config_rows = {
            (section, parameter): value
            for section, parameter, value in workbook[
                "Tester_Config"
            ].iter_rows(min_row=2, values_only=True)
        }
        assert config_rows[("run", "export_schema_version")] == "2"
        assert config_rows[("run", "signals_schema_version")] == "1"
        assert config_rows[("run", "signals_semantics")] == (
            "raw_st_analytics"
        )
        assert len(
            config_rows[("run", "tester_run_fingerprint")]
        ) == 64
    finally:
        workbook.close()

    full_sample_output = export_tester_results(
        results[:1],
        str(tmp_path / "reversal_active_full_sample_only.xlsx"),
        signals_df=signals,
        trade_filter_config=config,
        df=frame,
        export_diagnostics=False,
        export_signals=True,
        export_false_start=False,
        export_cycle=False,
        export_trades=False,
        add_timestamp=False,
    )
    full_sample_workbook = load_workbook(
        full_sample_output,
        read_only=True,
        data_only=True,
    )
    try:
        full_sample_rows = {
            (section, parameter): value
            for section, parameter, value in full_sample_workbook[
                "Tester_Config"
            ].iter_rows(min_row=2, values_only=True)
        }
        assert full_sample_rows[("run", "tester_run_fingerprint")] != (
            config_rows[("run", "tester_run_fingerprint")]
        )
    finally:
        full_sample_workbook.close()
