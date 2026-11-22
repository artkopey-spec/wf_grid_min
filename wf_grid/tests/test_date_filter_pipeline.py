from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest
import yaml

from wf_grid.pipeline.orchestrator import run_grid_pipeline
from wf_grid.tests.test_parallel_execution import (
    _build_zigzag_fixture_dataset, _write_tiny_yaml,
    TestSequentialVsParallelEquivalence as _Parity,
)
from wf_grid.tests.test_w8_wf_volume_integration import _volume_raw


def profile(tmp_path, mode="dates", full=False):
    csv = _build_zigzag_fixture_dataset(tmp_path, n_bars=760)
    bars = pd.read_csv(csv)
    bars["volume"] = 100 + np.arange(len(bars)) % 11
    bars.to_csv(csv, index=False)
    path = _write_tiny_yaml(tmp_path, csv, parallel_enabled=True, max_workers=2, fallback_to_sequential=True,
                            trade_filter_mode="enabled_fixture" if mode == "zigzag" else "absent")
    cfg = yaml.safe_load(path.read_text())
    tf = cfg.setdefault("trade_filter", {"enabled": True, "zigzag": {"enabled": False}})
    if mode == "volume":
        tf["volume"] = _volume_raw()
    if mode == "time":
        tf["time_filter"] = {"enabled": True, "windows": ["00:00-23:59"]}
    cfg["optimization"].update(atr_period_range=[10, 11], multiplier_range=[1.0, 1.0])
    dates = pd.to_datetime(bars["datetime"]).dt.strftime("%Y%m%d").unique()
    file = tmp_path / "dates.csv"
    file.write_text("<DATE>\n" + "\n".join(dates if full else dates[::2]))
    tf["date_filter"] = {"enabled": True, "file_path": str(file)}
    path.write_text(yaml.safe_dump(cfg))
    return path


@pytest.mark.parametrize("mode", ["dates", "time", "volume", "zigzag"])
@pytest.mark.parametrize("full", [False, True])
def test_three_execution_paths(tmp_path, monkeypatch, mode, full):
    path = profile(tmp_path, mode, full)
    def run(name, parallel):
        result = run_grid_pipeline(str(path), output_path=str(tmp_path / (name + ".xlsx")),
                                   parallel_enabled=parallel)
        assert result.error is None
        for frame in (result.step_train_long, result.step_oos_long):
            assert "runtime_error" not in set(frame.step_status)
        return result
    sequential = run("seq", False)
    worker = run("worker", True)
    monkeypatch.setenv("WF_GRID_FORCE_WORKER_CRASH", "exit")
    fallback = run("fallback", True)
    assert (sequential.execution_mode, worker.execution_mode, fallback.execution_mode) == (
        "sequential", "parallel", "parallel_then_fallback")
    for other in (worker, fallback):
        for name in _Parity._COMPARE_FRAMES:
            left, right = getattr(sequential, name), getattr(other, name)
            if left is None:
                assert right is None
            else:
                pd.testing.assert_frame_equal(left, right, check_exact=True)
    assert (sequential.config.date_filter_metadata["boundary_bars"] == 0) == full


def test_cli_from_other_cwd(tmp_path):
    config = profile(tmp_path)
    original = config.read_text()
    cfg = yaml.safe_load(original)
    cfg["trade_filter"]["date_filter"]["file_path"] = "data 1.csv"
    (tmp_path / "data 1.csv").write_text("invalid cwd decoy")
    config.write_text(yaml.safe_dump(cfg))
    output = tmp_path / "cli.xlsx"
    cmd = [sys.executable, str(Path(__file__).resolve().parents[2] / "run.py"),
           "--config", str(config), "--output", str(output), "--sequential"]
    result = subprocess.run(cmd, cwd=tmp_path, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert output.exists()
    output.unlink()
    config.write_text(original)
    (tmp_path / "dates.csv").write_text("<DATE>\n20230229")
    result = subprocess.run(cmd, cwd=tmp_path, capture_output=True)
    assert result.returncode != 0
    assert b"20230229" in result.stdout and b"line 2" in result.stdout
    assert not output.exists()


@pytest.mark.parametrize("capability,error", [
    ("reversal_confirmation", "reversal_confirmation_unsupported_pipeline"),
    ("wakeup_regime", "use tester pipeline"),
])
def test_calendar_preserves_pipeline_restrictions(tmp_path, capability, error):
    from wf_grid.config.loader import ConfigError, load_grid_config
    path = profile(tmp_path, "zigzag" if capability == "wakeup_regime" else "dates")
    cfg = yaml.safe_load(path.read_text())
    cfg["trade_filter"][capability] = (
        {"enabled": True, "confirm_bars": 1} if capability == "reversal_confirmation" else {"enabled": True})
    path.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ConfigError, match=error):
        load_grid_config(str(path))


@pytest.mark.parametrize("contents", [None, "<DATE>\n", "x\n20240101",
                                      "<DATE>\n19700101", "<DATE>\n20240101\n20230229"])
def test_calendar_failure_precedes_workers(tmp_path, monkeypatch, contents):
    path = profile(tmp_path)
    calendar = tmp_path / "dates.csv"
    if contents is None:
        calendar.unlink()
    else:
        calendar.write_text(contents)
    def unexpected_grid(*args, **kwargs):
        pytest.fail("invalid calendar reached grid execution")
    monkeypatch.setattr("wf_grid.pipeline.orchestrator._execute_wf_grid", unexpected_grid)
    report = tmp_path / "failed.xlsx"
    result = run_grid_pipeline(str(path), output_path=str(report))
    assert str(calendar) in result.error
    assert not report.exists()


def test_missing_worker_calendar_is_fatal(tmp_path, monkeypatch):
    from wf_grid.wf import _mp_helpers
    pack = _mp_helpers.pack_data
    def omit_boundary(data):
        return pack(data.drop(columns=["_date_boundary"]))
    monkeypatch.setattr(_mp_helpers, "pack_data", omit_boundary)
    report = tmp_path / "failed.xlsx"
    result = run_grid_pipeline(str(profile(tmp_path)), output_path=str(report))
    assert "calendar columns missing" in result.error
    assert not report.exists()


@pytest.mark.parametrize("mode", ["dates", "time", "volume", "zigzag"])
def test_full_calendar_matches_original_trades(tmp_path, mode):
    path = profile(tmp_path, mode, full=True)
    cfg = yaml.safe_load(path.read_text())
    cfg["optimization"]["atr_period_range"] = [10, 10]
    path.write_text(yaml.safe_dump(cfg))
    active = run_grid_pipeline(str(path), output_path=str(tmp_path / "full.xlsx"), parallel_enabled=False)
    assert active.error is None
    if mode == "dates":
        cfg.pop("trade_filter")
    else:
        cfg["trade_filter"].pop("date_filter")
    path.write_text(yaml.safe_dump(cfg))
    old = run_grid_pipeline(str(path), output_path=str(tmp_path / "old.xlsx"), parallel_enabled=False)
    assert old.error is None
    assert active.config.date_filter_metadata["boundary_bars"] == 0
    for name in ("trades_train", "trades_oos"):
        before, after = getattr(old, name), getattr(active, name)
        cols = ["entry_time", "exit_time", "entry_index", "exit_index", "direction", "entry_price",
                "exit_price", "commission_pct", "net_pnl_pct"]
        pd.testing.assert_frame_equal(before[cols], after[cols], check_exact=True, check_dtype=False)
    pd.testing.assert_frame_equal(old.step_train_long, active.step_train_long, atol=1e-12, rtol=1e-12)


def test_inactive_and_extra_dates(tmp_path, monkeypatch):
    path = profile(tmp_path, "time")
    cfg = yaml.safe_load(path.read_text())
    tf = cfg["trade_filter"]
    def run(label):
        path.write_text(yaml.safe_dump(cfg))
        result = run_grid_pipeline(str(path), output_path=str(tmp_path / (label + ".xlsx")),
                                   parallel_enabled=False)
        assert result.error is None
        return result

    active = run("active")
    with (tmp_path / "dates.csv").open("a") as stream:
        stream.write("\n19000101\n")
    extra = run("extra")
    assert active.config.date_filter_metadata["sha256"] != extra.config.date_filter_metadata["sha256"]
    pd.testing.assert_frame_equal(active.step_oos_long, extra.step_oos_long)
    pd.testing.assert_frame_equal(active.trades_train, extra.trades_train)

    def unexpected_read(*args):
        pytest.fail("inactive filter read the calendar")
    monkeypatch.setattr("wf_grid.pipeline.orchestrator.load_trade_calendar", unexpected_read)
    tf.pop("date_filter")
    original = run("absent")
    tf["date_filter"] = {"enabled": False, "file_path": "missing.csv"}
    disabled = run("disabled")
    tf["enabled"] = False
    tf["date_filter"]["enabled"] = True
    parent_off = run("parent-off")
    tf.pop("date_filter")
    parent_original = run("parent-original")
    for first, second in ((original, disabled), (parent_original, parent_off)):
        for name in _Parity._COMPARE_FRAMES:
            a, b = getattr(first, name), getattr(second, name)
            if a is not None or b is not None:
                pd.testing.assert_frame_equal(a, b, check_exact=True)


@pytest.mark.slow
def test_reference_sber_walk_forward(tmp_path):
    import math
    from openpyxl import load_workbook
    root = Path(__file__).resolve().parents[2]
    cfg = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    cfg["data"]["file_path"] = str(root / "data.csv")
    cfg["trade_filter"]["date_filter"]["enabled"] = True
    cfg["optimization"].update(atr_period_range=[10, 10], multiplier_range=[2, 2])
    path = tmp_path / "sber.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    result = run_grid_pipeline(str(path), output_path=str(tmp_path / "sber.xlsx"),
                               parallel_enabled=False)
    assert result.error is None
    assert result.diagnostics.n_wf_steps == 16
    assert math.ceil(result.config.gates.candidate.min_ok_ratio*16) == 11
    meta = result.config.date_filter_metadata
    assert (meta["allowed_bars"], meta["boundary_bars"]) == (147439, 157)
    assert meta["executed_date_closes_oos_all_grid_steps"] == 0
    assert result.step_train_long.train_start_idx.min() == 5009
    book = load_workbook(result.output_path, read_only=True)
    try:
        assert {"exit_phase", "exit_reason", "exit_global_index", "exit_time"} <= {
            cell.value for cell in next(book["WF_Trades"].rows)
        }
        assert any(meta["sha256"] in row for sheet in book
                   if "config" in sheet.title.lower() for row in sheet.values)
    finally:
        book.close()
