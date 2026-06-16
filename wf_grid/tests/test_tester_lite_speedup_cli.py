import argparse
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import supertrend_optimizer.cli.tester as tester


def _export_config(**overrides):
    config = {
        "diagnostics": False,
        "signals": False,
        "false_start": False,
        "cycle": False,
        "trades": False,
    }
    config.update(overrides)
    return config


@pytest.mark.parametrize(
    ("export_config", "expected"),
    [
        (_export_config(), False),
        (_export_config(false_start=True), False),
        (_export_config(diagnostics=True), True),
        (_export_config(signals=True), True),
        (_export_config(cycle=True), True),
        (_export_config(trades=True), True),
    ],
)
def test_resolve_collect_filter_diagnostics_formula(export_config, expected):
    assert tester._resolve_collect_filter_diagnostics(export_config) is expected


@pytest.mark.parametrize(
    ("export_config", "expected_collect"),
    [
        (_export_config(false_start=True), False),
        (_export_config(trades=True), True),
    ],
)
def test_legacy_tester_passes_export_formula_to_runner(
    tmp_path,
    monkeypatch,
    export_config,
    expected_collect,
):
    df = pd.DataFrame(
        {
            "open": [10.0, 11.0, 12.0, 13.0],
            "high": [11.0, 12.0, 13.0, 14.0],
            "low": [9.0, 10.0, 11.0, 12.0],
            "close": [10.5, 11.5, 12.5, 13.5],
            "volume": [100.0, 120.0, 110.0, 130.0],
        }
    )
    args = argparse.Namespace(
        config=None,
        out=str(tmp_path / "lite.xlsx"),
        atr=None,
        mult=None,
        mode=None,
        periods_per_year=None,
        annualization_basis=None,
        market=None,
        execution_model=None,
        exact_output_path=True,
    )
    trade_filter_config = object()
    params = {
        "atr_period": 2,
        "multiplier": 1.5,
        "trade_mode": "long",
        "commission": 0.0,
        "warmup_period": 0,
        "warmup_period_auto": False,
        "min_trades_required": 1,
        "annualization_factor": 252,
        "annualization_basis": None,
        "market": None,
        "execution_model": None,
        "period": True,
        "segmentation": {"mode": "legacy", "n_parts": 5},
        "export": export_config,
        "false_start_max_bars": 4,
        "trade_filter": trade_filter_config,
    }
    captured = {}
    fake_period = SimpleNamespace(
        period_label="100%",
        metrics={"num_trades": 0, "sum_pnl_pct": 0.0},
        result=SimpleNamespace(trend=np.zeros(len(df), dtype=np.int8)),
        effective_warmup=2,
        filter_diagnostics=None,
    )

    monkeypatch.setattr(tester, "validate_output_path", lambda path: None)
    monkeypatch.setattr(tester, "load_tester_config", lambda *args, **kwargs: {})
    monkeypatch.setattr(tester, "merge_cli_and_config", lambda *args: params)
    monkeypatch.setattr(tester, "validate_volume_filter_data", lambda df_arg, tf: df_arg)
    monkeypatch.setattr(tester, "is_volume_enabled", lambda tf: False)
    monkeypatch.setattr(tester, "is_zigzag_enabled", lambda tf: tf is trade_filter_config)
    monkeypatch.setattr(
        tester,
        "build_zigzag_global_stats",
        lambda **kwargs: SimpleNamespace(
            n_legs_total=0,
            global_median=0.0,
            candidate_trigger_threshold=0.0,
            candidate_trigger_source="insufficient_global_stats",
        ),
    )
    monkeypatch.setattr(
        tester,
        "resolve_periods_per_year_from_config",
        lambda **kwargs: 252.0,
    )

    def fake_run_all_periods(**kwargs):
        captured.update(kwargs)
        return [fake_period]

    def fake_export_tester_results(results, out, **kwargs):
        if not expected_collect:
            assert results[0].filter_diagnostics is None
        return out

    monkeypatch.setattr(tester, "run_all_periods", fake_run_all_periods)
    monkeypatch.setattr(tester, "export_tester_results", fake_export_tester_results)

    actual = tester.run_backtest_with_df(args, df, csv_path_for_metadata="data.csv")

    assert actual == args.out
    assert captured["trade_filter_config"] is trade_filter_config
    assert captured["collect_filter_diagnostics"] is expected_collect
