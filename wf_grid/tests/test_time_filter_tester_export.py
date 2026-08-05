from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import load_workbook

from supertrend_optimizer.core.trade_filter_config import (
    build_trade_filter_config_from_raw,
    collect_raw_user_keys,
    resolve_time_filter_in_place,
    resolve_volume_enabled_in_place,
    resolve_zigzag_enabled_in_place,
    validate_trade_filter,
)
from supertrend_optimizer.io.diagnostics_v2 import (
    build_diagnostics_v2_context,
    build_enabled_v2_sheets,
    resolve_diagnostics_v2_flags,
)
from supertrend_optimizer.io.excel_tester import (
    _build_filter_summary_block_df,
    _prepare_legacy_trades_df,
    export_tester_results,
)
from supertrend_optimizer.testing.runner import run_all_periods
from supertrend_optimizer.testing.signal_events import build_signal_events
from supertrend_optimizer.utils.enums import ExecutionModel


def _time_only_config():
    raw = {
        "enabled": True,
        "zigzag": {"enabled": False},
        "volume": {"enabled": False},
        "time_filter": {
            "enabled": True,
            "windows": ["09:00-09:05", "10:00-10:05"],
        },
    }
    keys = collect_raw_user_keys({"trade_filter": raw})
    config = build_trade_filter_config_from_raw(raw)
    resolve_zigzag_enabled_in_place(config, keys)
    resolve_volume_enabled_in_place(config, keys)
    errors: list[str] = []
    validate_trade_filter(
        config,
        errors,
        keys,
        caller_pipeline="tester",
    )
    assert errors == []
    resolve_time_filter_in_place(config, keys)
    return config


def _data() -> pd.DataFrame:
    index = pd.DatetimeIndex(
        [
            *pd.date_range("2026-01-01 08:58", periods=10, freq="min"),
            *pd.date_range("2026-01-02 09:58", periods=10, freq="min"),
        ]
    )
    base = np.linspace(100.0, 110.0, len(index))
    return pd.DataFrame(
        {
            "open": base,
            "high": base + 1.0,
            "low": base - 1.0,
            "close": base + np.sin(np.arange(len(index))),
        },
        index=index,
    )


def _results():
    return run_all_periods(
        _data(),
        atr_period=2,
        multiplier=2.0,
        trade_mode="revers",
        commission=0.0,
        warmup_period=2,
        trade_filter_config=_time_only_config(),
    )


def test_tester_time_only_precomputes_full_runtime_and_clears_slice_first_reset(
    monkeypatch,
) -> None:
    import supertrend_optimizer.testing.runner as runner

    calls = 0
    original = runner.resolve_time_filter_runtime

    def counted(**kwargs):
        nonlocal calls
        calls += 1
        return original(**kwargs)

    monkeypatch.setattr(runner, "resolve_time_filter_runtime", counted)
    results = _results()

    assert calls == 1
    assert len(results) == 5
    for period in results:
        assert period.filter_diagnostics["time_filter_reset_event"][0] == 0


def test_time_only_summary_and_diagnostics_have_no_fake_owner_fields() -> None:
    period = _results()[0]
    diagnostics = period.filter_diagnostics
    summary = period.filter_diagnostics_summary

    assert summary["mode"] == "time_only"
    assert summary["trade_filter_enabled"] is True
    assert summary["time_filter_enabled"] is True
    assert summary["time_filter_reset_count"] == int(
        diagnostics["time_filter_reset_event"].sum()
    )
    assert summary["time_filter_blocked_entry_count"] == int(
        np.sum(diagnostics["filter_block_reason"] == "time_filter_out_of_window")
    )
    assert summary["time_filter_forced_exit_count"] == int(
        (period.trades_df["exit_reason"] == "filter_time_reset").sum()
    )
    for key in (
        "trade_filter_state",
        "trade_filter_state_code",
        "trade_filter_trigger_source",
        "median_stop_triggered",
        "zz_leg_stop_triggered",
        "volume_regime",
        "volume_condition_allowed",
    ):
        assert key not in diagnostics


def test_diagnostics_v2_preserves_not_applicable_status_in_dashboard() -> None:
    from supertrend_optimizer.io.diagnostics_v2 import (
        _build_dashboard_sheet,
        _sheet,
    )

    cycle = _sheet(
        "Cycle_Summary",
        phase="A2",
        df=pd.DataFrame([{"Value": "not_applicable"}]),
        status="not_applicable",
        primary_source="trade_filter capabilities",
    )
    dashboard = _build_dashboard_sheet({"Cycle_Summary": cycle})
    row = dashboard.df[
        (dashboard.df["Source sheet"] == "Cycle_Summary")
        & (dashboard.df["Metric"] == "status")
    ].iloc[0]
    assert row["Value"] == "not_applicable"
    assert row["Status"] == "not_applicable"


def test_time_only_signals_trades_summary_and_cycles_are_capability_aware() -> None:
    results = _results()
    period = results[0]
    signals = build_signal_events(
        df=_data(),
        trend=period.result.trend,
        atr_period=2,
        trade_mode="revers",
        execution_model=ExecutionModel.OPEN_TO_OPEN,
        filter_diagnostics=period.filter_diagnostics,
    )
    assert "filter_decision" in signals.columns
    assert "filter_block_reason" in signals.columns
    assert "filter_state_at_signal" not in signals.columns
    assert "filter_trigger_source" not in signals.columns

    summary_df = _build_filter_summary_block_df(results)
    assert "Filter Mode" in summary_df.columns
    assert "Time Filter Blocked Entries" in summary_df.columns
    assert "Bars WAIT" not in summary_df.columns
    assert "Volume Blocked Starts" not in summary_df.columns

    empty_trades = _prepare_legacy_trades_df(
        pd.DataFrame(),
        filter_enabled=True,
        filter_diagnostics_available=True,
        filter_diagnostics=period.filter_diagnostics,
    )
    assert "Trade Close Reason" in empty_trades.columns
    assert "Entry Filter State" not in empty_trades.columns
    assert "Entry Trigger Source" not in empty_trades.columns

    ctx = build_diagnostics_v2_context(
        results,
        pr_100=period,
        df=_data(),
        signals_df=signals,
        run_metadata={},
        trade_filter_config=_time_only_config(),
        config_yaml_snapshot=None,
    )
    payloads = build_enabled_v2_sheets(
        ctx,
        resolve_diagnostics_v2_flags({}),
    )
    cycle = next(payload for payload in payloads if payload.name == "Cycle_Summary")
    assert cycle.status == "not_applicable"
    assert cycle.df.iloc[0]["Value"] == "not_applicable"


def test_canonical_batch_marks_equal_blocks_unsupported_and_keeps_legacy() -> None:
    module_path = (
        Path(__file__).resolve().parents[2]
        / "donor TESTER"
        / "run_batch_tester.py"
    )
    spec = importlib.util.spec_from_file_location("time_filter_batch_test", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    config = _time_only_config()
    assert module.equal_blocks_skip_metadata("equal_blocks", config) == {
        "mode": "equal_blocks",
        "status": "skipped_unsupported",
        "reason": "trade_filter_equal_blocks_not_supported",
    }
    assert module.equal_blocks_skip_metadata("legacy", config) is None
    assert module.equal_blocks_skip_metadata(
        "equal_blocks",
        SimpleDisabledFilter(),
    ) is None


class SimpleDisabledFilter:
    enabled = False


def test_time_only_excel_contains_public_and_effective_snapshots(tmp_path) -> None:
    output = tmp_path / "time_only.xlsx"
    actual = export_tester_results(
        _results(),
        str(output),
        trade_filter_config=_time_only_config(),
        df=_data(),
        export_diagnostics=False,
        export_signals=False,
        export_false_start=False,
        export_cycle=False,
        export_trades=False,
        add_timestamp=False,
    )

    workbook = load_workbook(actual, read_only=True)
    try:
        sheet = workbook["Tester_Config"]
        flat = {
            (section, parameter): value
            for section, parameter, value in sheet.iter_rows(
                min_row=2, values_only=True
            )
        }
        assert flat[
            (
                "run",
                "trade_filter_effective_snapshot.effective_mode",
            )
        ] == "time_only"
        assert flat[
            (
                "run",
                "trade_filter_effective_snapshot.canonical_windows_minutes[0][0]",
            )
        ] == "540"
        assert flat[
            (
                "run",
                "trade_filter_public_snapshot.trade_filter.time_filter.windows[0]",
            )
        ] == "09:00-09:05"
        assert not any(
            parameter.startswith("_") or "._" in parameter
            for section, parameter in flat
            if section == "run"
        )
    finally:
        workbook.close()
