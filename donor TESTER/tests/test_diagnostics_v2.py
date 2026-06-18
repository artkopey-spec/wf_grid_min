from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.cli.tester import load_tester_config
from supertrend_optimizer.io.diagnostics_v2 import (
    DIAGNOSTICS_V2_THRESHOLDS,
    DiagnosticsV2Context,
    RESERVED_FLAGS,
    build_enabled_v2_sheets,
    derive_trade_cycle_map,
    resolve_collect_filter_diagnostics,
    resolve_diagnostics_v2_flags,
)
from supertrend_optimizer.utils.exceptions import ConfigError


def _write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_diagnostics_v2_defaults_disabled_and_flags_empty():
    cfg = load_tester_config(None)

    assert cfg["export"]["diagnostics_v2"] is False
    assert cfg["export"]["diagnostics_v2_flags"] == {}


def test_export_unknown_top_level_key_remains_permissive(tmp_path):
    path = _write_config(
        tmp_path,
        """
export:
  diagnostics_v2: false
  unknown_future_key: true
""",
    )

    cfg = load_tester_config(str(path))

    assert cfg["export"]["diagnostics_v2"] is False


def test_diagnostics_v2_unknown_child_flag_fails():
    with pytest.raises(ConfigError, match="unknown key"):
        resolve_diagnostics_v2_flags({"not_a_sheet": True})


def test_diagnostics_v2_non_bool_child_flag_fails():
    with pytest.raises(ConfigError, match="must be a boolean"):
        resolve_diagnostics_v2_flags({"run_health": "true"})


def test_diagnostics_v2_reserved_true_fails_until_implemented():
    reserved = next(iter(RESERVED_FLAGS))

    with pytest.raises(ConfigError, match="reserved"):
        resolve_diagnostics_v2_flags({reserved: True})


def test_resolve_collect_filter_diagnostics_includes_diagnostics_v2():
    export = {
        "diagnostics": False,
        "signals": False,
        "cycle": False,
        "trades": False,
        "diagnostics_v2": True,
    }

    assert resolve_collect_filter_diagnostics(export) is True


def test_batch_resolver_preserves_legacy_default_when_v2_disabled():
    export = {
        "diagnostics": False,
        "signals": False,
        "cycle": False,
        "trades": False,
        "diagnostics_v2": False,
    }

    assert (
        resolve_collect_filter_diagnostics(
            export,
            preserve_legacy_batch_default=True,
        )
        is True
    )
    assert resolve_collect_filter_diagnostics(export) is False


def test_derive_trade_cycle_map_uses_legacy_entry_signal_mapping():
    fd = {
        "trade_filter_state": np.asarray(
            ["OFF", "ST_ACTIVE_FREEZE", "ST_ACTIVE_MONITORING", "OFF"],
            dtype=object,
        )
    }
    trades = pd.DataFrame(
        {
            "trade_id": [10, 11, 12],
            "entry_index": [2, 3, 4],
            "net_pnl_pct": [1.0, 0.5, -0.5],
        }
    )

    result = derive_trade_cycle_map(fd, trades, mode="zigzag")

    assert result.loc[0, "mapping_status"] == "mapped"
    assert result.loc[0, "cycle_id"] == 1
    assert result.loc[0, "cycle_age_at_entry"] == 0
    assert result.loc[0, "trade_idx_in_cycle"] == 1
    assert result.loc[0, "cycle_trade_count_at_entry"] == 0
    assert result.loc[1, "mapping_status"] == "mapped"
    assert result.loc[1, "trade_idx_in_cycle"] == 2
    assert result.loc[1, "cycle_trade_count_at_entry"] == 1
    assert result.loc[2, "mapping_status"] == "outside_cycle"


def test_derive_trade_cycle_map_volume_only_matches_legacy_cycle_boundaries():
    from supertrend_optimizer.io import excel_tester

    states = ["OFF", "ACTIVE_LONG", "ACTIVE_LONG", "OFF", "SUPPRESSED_SHORT", "OFF"]
    fd = {
        "trade_filter_state": np.asarray(states, dtype=object),
        "daily_reset_event": np.zeros(len(states), dtype=np.int8),
    }
    df = pd.DataFrame(
        {
            "close": np.arange(100.0, 100.0 + len(states)),
            "high": np.arange(101.0, 101.0 + len(states)),
            "low": np.arange(99.0, 99.0 + len(states)),
        },
        index=pd.date_range("2026-01-01", periods=len(states), freq="h"),
    )
    trades = pd.DataFrame({"trade_id": [1, 2], "entry_index": [2, 5]})

    old_cycle = excel_tester._build_volume_cycle_sheet_df(fd, df, trades)
    cycle_map = derive_trade_cycle_map(fd, trades, mode="volume_only")

    assert list(old_cycle["Start bar index"]) == [1, 4]
    assert list(old_cycle["End bar index"]) == [3, 5]
    assert cycle_map.loc[0, "mapping_status"] == "mapped"
    assert cycle_map.loc[0, "cycle_start_index"] == old_cycle.loc[0, "Start bar index"]
    assert cycle_map.loc[0, "cycle_end_index"] == old_cycle.loc[0, "End bar index"]
    assert cycle_map.loc[1, "mapping_status"] == "mapped"
    assert cycle_map.loc[1, "cycle_start_index"] == old_cycle.loc[1, "Start bar index"]
    assert cycle_map.loc[1, "cycle_end_index"] == old_cycle.loc[1, "End bar index"]


def test_derive_trade_cycle_map_missing_and_invalid_statuses():
    trades = pd.DataFrame({"trade_id": [1, 2], "entry_index": [np.nan, "bad"]})
    fd = {"trade_filter_state": np.asarray(["OFF", "ST_ACTIVE_FREEZE"], dtype=object)}

    missing_fd = derive_trade_cycle_map(None, trades, mode="zigzag")
    assert list(missing_fd["mapping_status"]) == ["missing_fd_100", "missing_fd_100"]

    result = derive_trade_cycle_map(fd, trades, mode="zigzag")

    assert list(result["mapping_status"]) == [
        "missing_entry_index",
        "invalid_entry_index",
    ]


def _diagnostics_context() -> DiagnosticsV2Context:
    df = pd.DataFrame(
        {
            "open": [100.0, 101.0, 102.0, 103.0],
            "high": [101.0, 104.0, 105.0, 104.0],
            "low": [99.0, 100.0, 101.0, 102.0],
            "close": [100.5, 103.0, 104.0, 103.5],
        },
        index=pd.to_datetime(
            [
                "2026-01-01 00:00",
                "2026-01-01 01:00",
                "2026-01-01 01:00",
                "2026-01-01 03:00",
            ]
        ),
    )
    trades = pd.DataFrame(
        {
            "trade_id": [1],
            "direction": ["long"],
            "entry_index": [2],
            "exit_index": [3],
            "entry_price": [102.0],
            "exit_price": [103.5],
            "gross_pnl_pct": [3.0],
            "net_pnl_pct": [2.5],
            "commission_pct": [0.5],
        }
    )
    fd = {
        "trade_filter_state": np.asarray(
            ["OFF", "ST_ACTIVE_FREEZE", "ST_ACTIVE_MONITORING", "OFF"],
            dtype=object,
        ),
        "filter_allowed_entry": np.asarray([0, 1, 1, 0], dtype=np.int8),
        "filter_block_reason": np.asarray(["", "", "median_gate", ""], dtype=object),
        "st_flip_dir": np.asarray([0, 1, 0, 0], dtype=np.int8),
        "candidate_threshold_ok": np.asarray([0, 1, 1, 0], dtype=np.int8),
        "wakeup_entry_all_ok": np.asarray([np.nan, np.nan, np.nan, np.nan]),
    }
    cycle_map = derive_trade_cycle_map(fd, trades, mode="zigzag")
    return DiagnosticsV2Context(
        period_results=[],
        pr_100=SimpleNamespace(
            metrics={"sum_pnl_pct": 2.5},
            effective_warmup=14,
            n_bars=4,
        ),
        df=df,
        trades_100=trades,
        signals_df=pd.DataFrame(
            {
                "signal_bar_index": [1, 2],
                "filter_decision": ["blocked", "allowed"],
                "filter_block_reason": ["explicit_signal_reason", ""],
            }
        ),
        fd_100=fd,
        filter_diagnostics_summary=None,
        run_metadata={"config_path": "cfg.yml", "csv_path": "data.csv"},
        trade_filter_config=None,
        config_yaml_snapshot=None,
        cycle_map=cycle_map,
        thresholds=DIAGNOSTICS_V2_THRESHOLDS,
    )


def _sheet_by_name(sheets, name: str):
    return next(sheet for sheet in sheets if sheet.name == name)


def test_a1_sheets_are_built_from_context_and_payload_cache():
    flags = resolve_diagnostics_v2_flags(
        {
            "dashboard": False,
            "filter_funnel": False,
            "filter_attribution": False,
            "trade_analytics": False,
            "equity_drawdown": False,
            "cycle_summary": False,
            "cost_sensitivity": False,
            "remediation": False,
        }
    )

    sheets = build_enabled_v2_sheets(_diagnostics_context(), flags)

    repro = _sheet_by_name(sheets, "Reproducibility").df
    assert "rows_count" in set(repro["Field"])
    health = _sheet_by_name(sheets, "Run_Health").df
    duplicate_row = health.loc[health["Check"] == "Duplicate timestamps"].iloc[0]
    assert duplicate_row["Status"] == "FAIL"
    sampled = _sheet_by_name(sheets, "FilterDiagnostics_sampled").df
    assert "filter_allowed_entry" in sampled.columns
    index = _sheet_by_name(sheets, "Index").df
    assert set(index["Sheet"]) == {
        "Reproducibility",
        "Run_Health",
        "FilterDiagnostics_sampled",
    }


def test_run_health_missing_expected_fd_length_is_skip():
    ctx = DiagnosticsV2Context(
        period_results=[],
        pr_100=SimpleNamespace(metrics={}, effective_warmup=0),
        df=pd.DataFrame(),
        trades_100=pd.DataFrame(),
        signals_df=pd.DataFrame(),
        fd_100={"trade_filter_state": np.asarray(["OFF"], dtype=object)},
        filter_diagnostics_summary=None,
        run_metadata={},
        trade_filter_config=None,
        config_yaml_snapshot=None,
        cycle_map=pd.DataFrame(),
        thresholds=DIAGNOSTICS_V2_THRESHOLDS,
    )
    flags = resolve_diagnostics_v2_flags(
        {
            "index": False,
            "reproducibility": False,
            "dashboard": False,
            "trade_analytics": False,
            "equity_drawdown": False,
            "filter_funnel": False,
            "filter_attribution": False,
            "cycle_summary": False,
            "cost_sensitivity": False,
            "remediation": False,
            "filter_diagnostics_sampled": False,
        }
    )

    sheets = build_enabled_v2_sheets(ctx, flags)

    health = _sheet_by_name(sheets, "Run_Health").df
    row = health.loc[
        health["Check"] == "Filter diagnostics array length consistency"
    ].iloc[0]
    assert row["Status"] == "SKIP"


def test_run_health_missing_summary_metric_is_skip_not_fail():
    ctx = _diagnostics_context()
    ctx = DiagnosticsV2Context(
        period_results=ctx.period_results,
        pr_100=SimpleNamespace(metrics={}, effective_warmup=ctx.pr_100.effective_warmup, n_bars=4),
        df=ctx.df,
        trades_100=ctx.trades_100,
        signals_df=ctx.signals_df,
        fd_100=ctx.fd_100,
        filter_diagnostics_summary=ctx.filter_diagnostics_summary,
        run_metadata=ctx.run_metadata,
        trade_filter_config=ctx.trade_filter_config,
        config_yaml_snapshot=ctx.config_yaml_snapshot,
        cycle_map=ctx.cycle_map,
        thresholds=ctx.thresholds,
    )
    flags = resolve_diagnostics_v2_flags(
        {
            "index": False,
            "reproducibility": False,
            "dashboard": False,
            "trade_analytics": False,
            "equity_drawdown": False,
            "filter_funnel": False,
            "filter_attribution": False,
            "cycle_summary": False,
            "cost_sensitivity": False,
            "remediation": False,
            "filter_diagnostics_sampled": False,
        }
    )

    sheets = build_enabled_v2_sheets(ctx, flags)

    health = _sheet_by_name(sheets, "Run_Health").df
    row = health.loc[health["Check"] == "Summary vs Trades net PnL"].iloc[0]
    assert row["Status"] == "SKIP"


def test_run_health_does_not_report_overall_pass_when_nothing_is_proven():
    ctx = DiagnosticsV2Context(
        period_results=[],
        pr_100=SimpleNamespace(metrics={}, effective_warmup=0),
        df=pd.DataFrame(),
        trades_100=pd.DataFrame(),
        signals_df=pd.DataFrame(),
        fd_100=None,
        filter_diagnostics_summary=None,
        run_metadata={},
        trade_filter_config=None,
        config_yaml_snapshot=None,
        cycle_map=pd.DataFrame(),
        thresholds=DIAGNOSTICS_V2_THRESHOLDS,
    )
    flags = resolve_diagnostics_v2_flags(
        {
            "index": False,
            "reproducibility": False,
            "dashboard": False,
            "trade_analytics": False,
            "equity_drawdown": False,
            "filter_funnel": False,
            "filter_attribution": False,
            "cycle_summary": False,
            "cost_sensitivity": False,
            "remediation": False,
            "filter_diagnostics_sampled": False,
        }
    )

    sheets = build_enabled_v2_sheets(ctx, flags)

    assert _sheet_by_name(sheets, "Run_Health").status == "INFO"


def test_run_health_ohlcv_nan_warn_warmup_info_and_cycle_coverage_statuses():
    ctx = _diagnostics_context()
    ctx.df.loc[ctx.df.index[0], "open"] = np.nan

    sheets = build_enabled_v2_sheets(
        ctx,
        resolve_diagnostics_v2_flags(
            {
                "index": False,
                "reproducibility": False,
                "dashboard": False,
                "trade_analytics": False,
                "equity_drawdown": False,
                "filter_funnel": False,
                "filter_attribution": False,
                "cycle_summary": False,
                "cost_sensitivity": False,
                "remediation": False,
                "filter_diagnostics_sampled": False,
            }
        ),
    )
    health = _sheet_by_name(sheets, "Run_Health").df
    assert health.loc[health["Check"] == "OHLCV NaN"].iloc[0]["Status"] == "WARN"
    assert health.loc[health["Check"] == "Warmup facts"].iloc[0]["Status"] == "INFO"
    assert health.loc[health["Check"] == "Cycle map coverage"].iloc[0]["Status"] == "PASS"

    skip_ctx = DiagnosticsV2Context(
        period_results=ctx.period_results,
        pr_100=ctx.pr_100,
        df=ctx.df,
        trades_100=ctx.trades_100,
        signals_df=ctx.signals_df,
        fd_100=ctx.fd_100,
        filter_diagnostics_summary=ctx.filter_diagnostics_summary,
        run_metadata=ctx.run_metadata,
        trade_filter_config=ctx.trade_filter_config,
        config_yaml_snapshot=ctx.config_yaml_snapshot,
        cycle_map=pd.DataFrame(),
        thresholds=ctx.thresholds,
    )
    skip_sheets = build_enabled_v2_sheets(
        skip_ctx,
        resolve_diagnostics_v2_flags(
            {
                "index": False,
                "reproducibility": False,
                "dashboard": False,
                "trade_analytics": False,
                "equity_drawdown": False,
                "filter_funnel": False,
                "filter_attribution": False,
                "cycle_summary": False,
                "cost_sensitivity": False,
                "remediation": False,
                "filter_diagnostics_sampled": False,
            }
        ),
    )
    skip_health = _sheet_by_name(skip_sheets, "Run_Health").df
    assert skip_health.loc[skip_health["Check"] == "Cycle map coverage"].iloc[0]["Status"] == "SKIP"


def test_run_health_cycle_coverage_accepts_outside_cycle_as_proven_status():
    ctx = _diagnostics_context()
    cycle_map = ctx.cycle_map.copy()
    cycle_map.loc[0, "mapping_status"] = "outside_cycle"
    ctx = DiagnosticsV2Context(
        period_results=ctx.period_results,
        pr_100=ctx.pr_100,
        df=ctx.df,
        trades_100=ctx.trades_100,
        signals_df=ctx.signals_df,
        fd_100=ctx.fd_100,
        filter_diagnostics_summary=ctx.filter_diagnostics_summary,
        run_metadata=ctx.run_metadata,
        trade_filter_config=ctx.trade_filter_config,
        config_yaml_snapshot=ctx.config_yaml_snapshot,
        cycle_map=cycle_map,
        thresholds=ctx.thresholds,
    )

    sheets = build_enabled_v2_sheets(
        ctx,
        resolve_diagnostics_v2_flags(
            {
                "index": False,
                "reproducibility": False,
                "dashboard": False,
                "trade_analytics": False,
                "equity_drawdown": False,
                "filter_funnel": False,
                "filter_attribution": False,
                "cycle_summary": False,
                "cost_sensitivity": False,
                "remediation": False,
                "filter_diagnostics_sampled": False,
            }
        ),
    )

    health = _sheet_by_name(sheets, "Run_Health").df
    row = health.loc[health["Check"] == "Cycle map coverage"].iloc[0]
    assert row["Status"] == "PASS"


def test_a2_sheets_are_built_from_trades_cycle_map_and_cost_model():
    flags = resolve_diagnostics_v2_flags(
        {
            "dashboard": False,
            "filter_funnel": False,
            "filter_attribution": False,
            "remediation": False,
            "reproducibility": False,
            "run_health": False,
            "filter_diagnostics_sampled": False,
        }
    )

    sheets = build_enabled_v2_sheets(_diagnostics_context(), flags)

    trades = _sheet_by_name(sheets, "Trade_Analytics").df
    assert trades.loc[0, "quality_status"] == "ok"
    assert trades.loc[0, "mfe_pct"] > 0
    assert trades.loc[0, "mapping_status"] == "mapped"
    drawdown = _sheet_by_name(sheets, "Equity_Drawdown").df
    assert "Trade-equity drawdown excludes intratrade" in " ".join(
        drawdown["Notes"].astype(str)
    )
    cycle = _sheet_by_name(sheets, "Cycle_Summary").df
    assert "trades_mapped" in set(cycle["Metric"])
    cost = _sheet_by_name(sheets, "Cost_Sensitivity").df
    assert "gross_available" in set(cost["cost_model_status"])
    assert "remaining additional bps" in " ".join(cost["notes"].astype(str))


def test_trade_analytics_short_mfe_mae_formulas():
    ctx = _diagnostics_context()
    df = pd.DataFrame(
        {
            "open": [100.0, 100.0, 100.0],
            "high": [101.0, 106.0, 104.0],
            "low": [99.0, 96.0, 98.0],
            "close": [100.0, 101.0, 99.0],
        },
        index=pd.date_range("2026-01-01", periods=3, freq="h"),
    )
    trades = pd.DataFrame(
        {
            "trade_id": [1],
            "direction": ["short"],
            "entry_index": [1],
            "exit_index": [2],
            "entry_price": [100.0],
            "net_pnl_pct": [1.0],
        }
    )
    short_ctx = DiagnosticsV2Context(
        period_results=[],
        pr_100=SimpleNamespace(metrics={"sum_pnl_pct": 1.0}, effective_warmup=0, n_bars=3),
        df=df,
        trades_100=trades,
        signals_df=pd.DataFrame(),
        fd_100=ctx.fd_100,
        filter_diagnostics_summary=None,
        run_metadata={},
        trade_filter_config=None,
        config_yaml_snapshot=None,
        cycle_map=pd.DataFrame(
            {
                "trade_id": [1],
                "cycle_id": [1],
                "trade_idx_in_cycle": [1],
                "cycle_age_at_entry": [0],
                "cycle_trade_count_at_entry": [0],
                "is_in_cycle": [True],
                "cycle_start_index": [0],
                "cycle_end_index": [2],
                "mapping_status": ["mapped"],
            }
        ),
        thresholds=DIAGNOSTICS_V2_THRESHOLDS,
    )
    flags = resolve_diagnostics_v2_flags(
        {
            "index": False,
            "reproducibility": False,
            "dashboard": False,
            "run_health": False,
            "equity_drawdown": False,
            "filter_funnel": False,
            "filter_attribution": False,
            "cycle_summary": False,
            "cost_sensitivity": False,
            "remediation": False,
            "filter_diagnostics_sampled": False,
        }
    )

    sheets = build_enabled_v2_sheets(short_ctx, flags)

    analytics = _sheet_by_name(sheets, "Trade_Analytics").df
    assert analytics.loc[0, "quality_status"] == "ok"
    assert analytics.loc[0, "mfe_pct"] == pytest.approx(4.0)
    assert analytics.loc[0, "mae_pct"] == pytest.approx(-6.0)


def test_a3_sheets_are_conservative_and_cache_based():
    sheets = build_enabled_v2_sheets(_diagnostics_context(), resolve_diagnostics_v2_flags({}))

    funnel = _sheet_by_name(sheets, "Filter_Funnel").df
    assert "percent_from_previous" not in set(funnel.columns)
    missing_gate = funnel.loc[funnel["source_column"] == "volume_condition_allowed"].iloc[0]
    assert missing_gate["status"] == "SKIP"
    wakeup_gate = funnel.loc[funnel["source_column"] == "wakeup_entry_all_ok"].iloc[0]
    assert wakeup_gate["status"] == "SKIP"
    assert "Mode D" in wakeup_gate["notes"]

    attribution = _sheet_by_name(sheets, "Filter_Attribution").df
    text = " ".join(str(value) for value in attribution.to_numpy().ravel())
    assert "fixed-horizon close-to-close proxies" in text
    for banned in ("Saved PnL", "Lost PnL", "Net filter value"):
        assert banned not in text
    assert "explicit_signal_reason" in set(attribution["Reason"])

    dashboard = _sheet_by_name(sheets, "Dashboard").df
    dash_text = " ".join(str(value) for value in dashboard.to_numpy().ravel()).lower()
    assert "p-value" not in dash_text
    assert "significance" not in dash_text
    assert "Run_Health" in set(dashboard["Source sheet"])

    remediation = _sheet_by_name(sheets, "Remediation").df
    assert list(remediation.columns) == [
        "Priority",
        "Symptom",
        "Detection metric",
        "Observed",
        "Threshold",
        "Likely cause",
        "Parameter family",
        "Suggested action",
        "Source sheet",
        "Confidence",
        "Status",
    ]
    assert set(remediation["Confidence"]) == {"low"}


def test_filter_funnel_volume_only_mode_skips_zigzag_and_wakeup_gates():
    ctx = _diagnostics_context()
    volume_ctx = DiagnosticsV2Context(
        period_results=ctx.period_results,
        pr_100=ctx.pr_100,
        df=ctx.df,
        trades_100=ctx.trades_100,
        signals_df=ctx.signals_df,
        fd_100={
            **ctx.fd_100,
            "volume_condition_allowed": np.asarray([1, 0, 1, 0], dtype=np.int8),
        },
        filter_diagnostics_summary=ctx.filter_diagnostics_summary,
        run_metadata=ctx.run_metadata,
        trade_filter_config=SimpleNamespace(
            zigzag=SimpleNamespace(enabled=False),
            volume=SimpleNamespace(enabled=True),
        ),
        config_yaml_snapshot=ctx.config_yaml_snapshot,
        cycle_map=ctx.cycle_map,
        thresholds=ctx.thresholds,
    )
    flags = resolve_diagnostics_v2_flags(
        {
            "index": False,
            "reproducibility": False,
            "dashboard": False,
            "run_health": False,
            "trade_analytics": False,
            "equity_drawdown": False,
            "filter_attribution": False,
            "cycle_summary": False,
            "cost_sensitivity": False,
            "remediation": False,
            "filter_diagnostics_sampled": False,
        }
    )

    sheets = build_enabled_v2_sheets(volume_ctx, flags)

    funnel = _sheet_by_name(sheets, "Filter_Funnel").df
    assert funnel.loc[funnel["source_column"] == "candidate_threshold_ok"].iloc[0]["status"] == "SKIP"
    assert funnel.loc[funnel["source_column"] == "wakeup_entry_all_ok"].iloc[0]["status"] == "SKIP"
    assert funnel.loc[funnel["source_column"] == "volume_condition_allowed"].iloc[0]["status"] == "INFO"


def test_representative_builders_do_not_mutate_context_frames():
    ctx = _diagnostics_context()
    original_df = ctx.df.copy(deep=True)
    original_trades = ctx.trades_100.copy(deep=True)
    original_signals = ctx.signals_df.copy(deep=True)
    original_cycle_map = ctx.cycle_map.copy(deep=True)

    build_enabled_v2_sheets(ctx, resolve_diagnostics_v2_flags({}))

    pd.testing.assert_frame_equal(ctx.df, original_df)
    pd.testing.assert_frame_equal(ctx.trades_100, original_trades)
    pd.testing.assert_frame_equal(ctx.signals_df, original_signals)
    pd.testing.assert_frame_equal(ctx.cycle_map, original_cycle_map)


def test_filter_attribution_skips_ambiguous_blocked_universe():
    ctx = _diagnostics_context()
    ctx = DiagnosticsV2Context(
        period_results=ctx.period_results,
        pr_100=ctx.pr_100,
        df=ctx.df,
        trades_100=pd.DataFrame(),
        signals_df=pd.DataFrame(),
        fd_100={"trade_filter_state": ctx.fd_100["trade_filter_state"]},
        filter_diagnostics_summary=None,
        run_metadata=ctx.run_metadata,
        trade_filter_config=None,
        config_yaml_snapshot=None,
        cycle_map=pd.DataFrame(),
        thresholds=ctx.thresholds,
    )
    flags = resolve_diagnostics_v2_flags(
        {
            "index": False,
            "reproducibility": False,
            "dashboard": False,
            "run_health": False,
            "trade_analytics": False,
            "equity_drawdown": False,
            "filter_funnel": False,
            "cycle_summary": False,
            "cost_sensitivity": False,
            "remediation": False,
            "filter_diagnostics_sampled": False,
        }
    )

    sheets = build_enabled_v2_sheets(ctx, flags)

    attribution = _sheet_by_name(sheets, "Filter_Attribution").df
    assert "SKIP" in set(attribution["Status"])
    assert "no explicit event universe available" in " ".join(attribution["Notes"].astype(str))


def test_filter_attribution_allowed_entries_use_actual_entry_index():
    df = pd.DataFrame({"close": [100.0, 110.0, 121.0]})
    ctx = DiagnosticsV2Context(
        period_results=[],
        pr_100=SimpleNamespace(metrics={}, effective_warmup=0, n_bars=3),
        df=df,
        trades_100=pd.DataFrame({"entry_index": [1]}),
        signals_df=pd.DataFrame(),
        fd_100=None,
        filter_diagnostics_summary=None,
        run_metadata={},
        trade_filter_config=None,
        config_yaml_snapshot=None,
        cycle_map=pd.DataFrame(),
        thresholds=DIAGNOSTICS_V2_THRESHOLDS,
    )
    flags = resolve_diagnostics_v2_flags(
        {
            "index": False,
            "reproducibility": False,
            "dashboard": False,
            "run_health": False,
            "trade_analytics": False,
            "equity_drawdown": False,
            "filter_funnel": False,
            "cycle_summary": False,
            "cost_sensitivity": False,
            "remediation": False,
            "filter_diagnostics_sampled": False,
        }
    )

    sheets = build_enabled_v2_sheets(ctx, flags)

    attribution = _sheet_by_name(sheets, "Filter_Attribution").df
    row = attribution.loc[
        (attribution["Universe"] == "allowed_entries")
        & (attribution["Horizon"] == 1)
    ].iloc[0]
    assert row["Mean forward return pct"] == pytest.approx(10.0)


def test_filter_diagnostics_sampled_caps_rows_for_large_fd():
    n = 5000
    df = pd.DataFrame({"close": np.arange(float(n)) + 100.0})
    trades = pd.DataFrame({"entry_index": np.arange(0, n, 10), "exit_index": np.arange(1, n + 1, 10)[:500]})
    fd = {
        "trade_filter_state": np.asarray(["OFF"] * n, dtype=object),
        "filter_allowed_entry": np.zeros(n, dtype=np.int8),
    }
    ctx = DiagnosticsV2Context(
        period_results=[],
        pr_100=SimpleNamespace(metrics={}, effective_warmup=0, n_bars=n),
        df=df,
        trades_100=trades,
        signals_df=pd.DataFrame(),
        fd_100=fd,
        filter_diagnostics_summary=None,
        run_metadata={},
        trade_filter_config=None,
        config_yaml_snapshot=None,
        cycle_map=pd.DataFrame(),
        thresholds=DIAGNOSTICS_V2_THRESHOLDS,
    )
    flags = resolve_diagnostics_v2_flags(
        {
            "index": False,
            "reproducibility": False,
            "dashboard": False,
            "run_health": False,
            "trade_analytics": False,
            "equity_drawdown": False,
            "filter_funnel": False,
            "filter_attribution": False,
            "cycle_summary": False,
            "cost_sensitivity": False,
            "remediation": False,
        }
    )

    sheets = build_enabled_v2_sheets(ctx, flags)

    sampled = _sheet_by_name(sheets, "FilterDiagnostics_sampled").df
    assert len(sampled) <= 2000


def test_diagnostics_v2_static_no_engine_backtest_signal_generation_calls():
    module_path = Path(__file__).resolve().parents[2] / "donor" / "supertrend_optimizer" / "io" / "diagnostics_v2.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    forbidden_modules = {
        "supertrend_optimizer.testing.runner",
        "supertrend_optimizer.testing.signal_events",
        "supertrend_optimizer.core.backtest",
        "supertrend_optimizer.engine.run",
    }
    forbidden_call_names = {
        "run_all_periods",
        "run_period",
        "build_signal_events",
        "run_single_backtest",
    }
    imported_forbidden_aliases: set[str] = set()
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in forbidden_modules:
                    violations.append(alias.name)
                    imported_forbidden_aliases.add(alias.asname or alias.name.split(".")[-1])
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in forbidden_modules:
                violations.append(module)
                for alias in node.names:
                    imported_forbidden_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in forbidden_call_names:
                violations.append(func.id)
            elif (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id in imported_forbidden_aliases
            ):
                violations.append(f"{func.value.id}.{func.attr}")
    assert violations == []
