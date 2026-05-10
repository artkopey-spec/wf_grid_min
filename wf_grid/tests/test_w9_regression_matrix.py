from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.core.trade_filter_config import (
    build_trade_filter_config_from_raw,
    collect_raw_user_keys,
    resolve_exit_b_immediate_off_in_place,
    resolve_exit_off_mode_in_place,
    resolve_time_filter_in_place,
    resolve_trade_filter_mode_in_place,
    resolve_volume_defaults_in_place,
    resolve_volume_enabled_in_place,
    resolve_zigzag_enabled_in_place,
    validate_trade_filter,
)
from supertrend_optimizer.core.volume_metrics import build_volume_global_metrics
from supertrend_optimizer.core.zigzag_st_filter import build_zigzag_global_stats
from supertrend_optimizer.engine.run import run_single_backtest
from supertrend_optimizer.utils.enums import ExecutionModel

from wf_grid.baseline.fingerprint import compute_pipeline_fingerprint
from wf_grid.config.loader import load_grid_config
from wf_grid.pipeline.orchestrator import run_grid_pipeline
from wf_grid.tests import test_zigzag_apply_characterization as w4_golden


MODES = ("baseline", "zigzag_only", "standalone_volume", "zigzag_plus_volume")

_VOLUME_SNAPSHOT_KEYS = {
    "volume_filter_enabled",
    "volume_filter_mode",
    "volume_short_window",
    "volume_baseline_window",
    "volume_threshold_ratio",
    "volume_regime_low_ratio",
    "volume_regime_high_ratio",
    "volume_direction_lookback_bars",
}

_VOLUME_SUMMARY_COLUMNS = [
    "n_volume_blocked_start_attempts",
    "n_volume_warmup_blocked_start_attempts",
    "n_volume_below_baseline_blocked_start_attempts",
    "n_volume_above_baseline_blocked_start_attempts",
    "n_volume_baseline_zero_blocked_start_attempts",
    "n_volume_direction_warmup_blocked_start_attempts",
    "n_volume_unknown_direction_blocked_start_attempts",
    "n_volume_trade_mode_disallowed_direction_blocked_start_attempts",
    "avg_median_relative_volume",
    "n_volume_started_cycles",
]

_ZIGZAG_ONLY_COLUMNS = {
    "trigger_count_candidate_threshold",
    "trigger_count_confirmed_median",
    "trigger_count_both",
    "median_stop_triggered_count",
    "zz_leg_stop_triggered_count",
    "exit_off_mode",
    "exit_off_zz_leg_count",
}


def _df(n: int = 96) -> pd.DataFrame:
    x = np.linspace(0.0, 10.0 * np.pi, n)
    close = 100.0 + np.sin(x) * 4.0 + np.linspace(0.0, 5.0, n)
    open_ = np.r_[close[0], close[:-1]]
    volume = 100.0 + (np.sin(np.arange(n) / 2.0) > 0).astype(float) * 45.0
    return pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(open_, close) + 1.0,
            "low": np.minimum(open_, close) - 1.0,
            "close": close,
            "volume": volume,
        },
        index=pd.date_range("2025-01-01", periods=n, freq="min"),
    )


def _volume_block() -> str:
    return """
  volume:
    enabled: true
    mode: volume_A
    short_window: 2
    baseline_window: 5
    threshold_ratio: 0.98
    regime_low_ratio: 0.8
    regime_high_ratio: 1.2
    direction_lookback_bars: 1
"""


def _zigzag_block(*, include_volume: bool) -> str:
    volume = _volume_block() if include_volume else ""
    return f"""
trade_filter:
  enabled: true
  type: zigzag_st_mode
  zigzag:
    enabled: true
    mode: A+B
    reversal_threshold: 0.015
    candidate_trigger_threshold: 0.01
    local_window: 3
  lifecycle:
    freeze_confirmed_legs: 1
{volume}"""


def _trade_filter_yaml(mode: str) -> str:
    if mode == "baseline":
        return ""
    if mode == "zigzag_only":
        return _zigzag_block(include_volume=False)
    if mode == "standalone_volume":
        return f"""
trade_filter:
  enabled: true
{_volume_block()}"""
    if mode == "zigzag_plus_volume":
        return _zigzag_block(include_volume=True)
    raise KeyError(mode)


def _write_config(tmp_path: Path, mode: str, *, parallel: bool) -> Path:
    data_path = tmp_path / "data.csv"
    if not data_path.exists():
        _df().to_csv(data_path)

    cfg_path = tmp_path / f"{mode}_{'parallel' if parallel else 'sequential'}.yaml"
    cfg_path.write_text(
        f"""
schema_version: 1
data:
  file_path: "{data_path.as_posix()}"
  periods_per_year: 365
optimization:
  atr_period_range: [5, 5]
  multiplier_range: [1.8, 1.8]
  multiplier_step: 1.0
  trade_mode: revers
backtest:
  commission: 0.0
  min_trades_required: 0
validation:
  warmup_period: 0
  walk_forward:
    train_size: 40m
    test_size: 12m
    step_size: 12m
    min_train_bars: 10
    min_test_bars: 6
execution:
  parallel_enabled: {str(parallel).lower()}
  max_workers: 2
export:
  retain_per_bar_filter_diagnostics: false
{_trade_filter_yaml(mode)}
""",
        encoding="utf-8",
    )
    return cfg_path


def _run_matrix_case(tmp_path: Path, mode: str, *, parallel: bool):
    cfg_path = _write_config(tmp_path, mode, parallel=parallel)
    out_path = tmp_path / f"{mode}_{'par' if parallel else 'seq'}.xlsx"
    result = run_grid_pipeline(str(cfg_path), output_path=str(out_path))
    assert result.error is None
    assert result.step_oos_long is not None
    return result


def _build_tf(raw_tf: dict, *, caller: str = "wf_grid"):
    raw = {"trade_filter": raw_tf}
    raw_user_keys = collect_raw_user_keys(raw)
    cfg = build_trade_filter_config_from_raw(raw_tf)
    resolve_zigzag_enabled_in_place(cfg, raw_user_keys)
    resolve_volume_enabled_in_place(cfg, raw_user_keys)
    errors: list[str] = []
    validate_trade_filter(cfg, errors, raw_user_keys, caller_pipeline=caller)
    assert errors == []
    resolve_trade_filter_mode_in_place(cfg, raw_user_keys)
    resolve_exit_off_mode_in_place(cfg, raw_user_keys)
    resolve_exit_b_immediate_off_in_place(cfg, raw_user_keys)
    resolve_time_filter_in_place(cfg, raw_user_keys)
    resolve_volume_defaults_in_place(cfg, raw_user_keys)
    return cfg


def _direct_arrays(mode: str):
    df = _df()
    open_ = df["open"].to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()
    kwargs = dict(
        open_prices=open_,
        high=high,
        low=low,
        close=close,
        index=df.index,
        atr_period=5,
        multiplier=1.8,
        trade_mode="revers",
        commission=0.0,
        warmup_period=0,
        periods_per_year=365.0,
        min_trades_required=0,
        extract_trades_flag=True,
        execution_model=ExecutionModel.OPEN_TO_OPEN,
    )
    if mode == "baseline":
        return run_single_backtest(**kwargs)
    if mode == "baseline_disabled":
        return run_single_backtest(
            **kwargs,
            trade_filter_config=_build_tf({"enabled": False}),
        )
    if mode == "standalone_volume":
        tf = _build_tf({
            "enabled": True,
            "volume": {
                "enabled": True,
                "mode": "volume_A",
                "short_window": 2,
                "baseline_window": 5,
                "threshold_ratio": 0.98,
                "regime_low_ratio": 0.8,
                "regime_high_ratio": 1.2,
                "direction_lookback_bars": 1,
            },
        })
        runtime = build_volume_global_metrics(
            df["volume"].to_numpy(),
            close,
            tf.volume,
        )
        return run_single_backtest(
            **kwargs,
            trade_filter_config=tf,
            volume_runtime=runtime,
        )
    raise KeyError(mode)


def _assert_volume_summary_invariants(df: pd.DataFrame) -> None:
    present = [c for c in _VOLUME_SUMMARY_COLUMNS if c in df.columns]
    assert present
    for col in present:
        series = pd.to_numeric(df[col], errors="coerce")
        assert not series.isna().any(), f"{col} contains NaN"
        assert (series >= 0).all(), f"{col} contains negative values"


def _assert_no_volume_leak(result) -> None:
    assert result.step_oos_long is not None
    assert not any(c.startswith("n_volume_") for c in result.step_oos_long.columns)
    fp = compute_pipeline_fingerprint(result)
    assert _VOLUME_SNAPSHOT_KEYS.isdisjoint(fp.metadata)


def _assert_volume_snapshot(result) -> None:
    fp = compute_pipeline_fingerprint(result)
    assert _VOLUME_SNAPSHOT_KEYS <= set(fp.metadata)
    assert "grid_point_id" not in fp.metadata


def _assert_zigzag_golden_snapshots() -> None:
    for case_id in w4_golden.CASES:
        observed = w4_golden._run_case(case_id)
        expected = w4_golden._load_snapshot(case_id)
        assert observed["positions"] == expected["positions"]
        np.testing.assert_array_equal(
            np.array(observed["returns"], dtype=object),
            np.array(expected["returns"], dtype=object),
        )
        assert observed["filter_diagnostics_summary"] == expected["filter_diagnostics_summary"]
        assert observed["trades"] == expected["trades"]


@pytest.mark.parametrize("mode", MODES)
def test_regression_matrix_sequential(tmp_path, mode):
    result = _run_matrix_case(tmp_path, mode, parallel=False)
    cols = set(result.step_oos_long.columns)

    if mode == "baseline":
        baseline = _direct_arrays("baseline")
        disabled = _direct_arrays("baseline_disabled")
        np.testing.assert_array_equal(baseline.positions, disabled.positions)
        np.testing.assert_array_equal(baseline.returns, disabled.returns)
        np.testing.assert_array_equal(baseline.equity_curve, disabled.equity_curve)
        _assert_no_volume_leak(result)
        assert _ZIGZAG_ONLY_COLUMNS.isdisjoint(cols)
    elif mode == "zigzag_only":
        _assert_zigzag_golden_snapshots()
        _assert_no_volume_leak(result)
        assert cols & _ZIGZAG_ONLY_COLUMNS
    elif mode == "standalone_volume":
        direct = _direct_arrays("standalone_volume")
        assert np.any(direct.positions != 0)
        assert cols & set(_VOLUME_SUMMARY_COLUMNS)
        assert _ZIGZAG_ONLY_COLUMNS.isdisjoint(cols)
        _assert_volume_snapshot(result)
        _assert_volume_summary_invariants(result.step_oos_long)
    elif mode == "zigzag_plus_volume":
        assert cols & _ZIGZAG_ONLY_COLUMNS
        assert cols & set(_VOLUME_SUMMARY_COLUMNS)
        _assert_volume_snapshot(result)
        _assert_volume_summary_invariants(result.step_oos_long)


@pytest.mark.parametrize("mode", MODES)
def test_regression_matrix_parallel(tmp_path, mode):
    seq = _run_matrix_case(tmp_path, mode, parallel=False)
    par = _run_matrix_case(tmp_path, mode, parallel=True)

    if mode in {"standalone_volume", "zigzag_plus_volume"}:
        pd.testing.assert_frame_equal(
            seq.step_oos_long[_VOLUME_SUMMARY_COLUMNS].reset_index(drop=True),
            par.step_oos_long[_VOLUME_SUMMARY_COLUMNS].reset_index(drop=True),
        )
        _assert_volume_snapshot(seq)
        _assert_volume_snapshot(par)
        _assert_volume_summary_invariants(par.step_oos_long)
    else:
        _assert_no_volume_leak(seq)
        _assert_no_volume_leak(par)

    if mode == "standalone_volume":
        assert _ZIGZAG_ONLY_COLUMNS.isdisjoint(set(par.step_oos_long.columns))
    if mode in {"zigzag_only", "zigzag_plus_volume"}:
        _assert_zigzag_golden_snapshots()


def test_mandatory_golden_and_smoke_tests_are_discoverable():
    tests_dir = Path(__file__).parent
    golden_test = tests_dir / "test_zigzag_apply_characterization.py"
    smoke_test = tests_dir / "test_volume_metrics_smoke.py"
    snapshots = tests_dir / "golden_snapshots"

    assert golden_test.name.startswith("test_") and golden_test.exists()
    assert smoke_test.name.startswith("test_") and smoke_test.exists()
    assert snapshots.is_dir()
    assert len(list(snapshots.glob("*.json"))) >= 8

    golden_src = golden_test.read_text(encoding="utf-8")
    smoke_src = smoke_test.read_text(encoding="utf-8")
    assert "def test_zigzag_apply_characterization_matches_golden_snapshot" in golden_src
    assert "100_000" in smoke_src or "100000" in smoke_src


def test_zigzag_golden_snapshot_files_are_valid_json():
    snapshot_dir = Path(__file__).with_name("golden_snapshots")
    for path in snapshot_dir.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert {"positions", "returns", "filter_diagnostics_summary", "trades"} <= set(payload)
