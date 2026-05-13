"""Fast-export operational tools and parity smoke tests."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import openpyxl
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module(path: Path, name: str):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_tiny_ohlc(path: Path, n: int = 80) -> None:
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    base = pd.Series(range(n), dtype="float64").to_numpy()
    close = 100.0 + (base % 11) * 0.2 + base * 0.03
    open_ = pd.Series(close).shift(1).fillna(close[0]).to_numpy()
    high = pd.Series([max(o, c) + 0.5 for o, c in zip(open_, close)]).to_numpy()
    low = pd.Series([min(o, c) - 0.5 for o, c in zip(open_, close)]).to_numpy()
    df = pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1000 + (base.astype(int) % 7) * 10,
        },
        index=idx,
    )
    df.to_csv(path)


def _write_fast_config(path: Path) -> None:
    path.write_text(
        """
supertrend:
  atr_period: 5
  multiplier: 2.0
trade_mode: revers
commission: 0.0
warmup_period: 0
warmup_period_auto: false
periods_per_year: 252
annualization_basis: trading
execution_model: open_to_open
min_trades_required: 0
period: false
segmentation:
  mode: legacy
  n_parts: 4
export:
  diagnostics: false
  signals: false
  false_start: false
  cycle: false
  trades: false
  false_start_max_bars: 4
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _env() -> dict[str, str]:
    env = os.environ.copy()
    donor = str((REPO_ROOT / "donor").resolve())
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = donor if not existing else donor + os.pathsep + existing
    return env


def _latest_xlsx(directory: Path) -> Path:
    files = sorted(directory.glob("*.xlsx"), key=lambda p: p.stat().st_mtime)
    assert files, f"No XLSX files in {directory}"
    return files[-1]


def _sheet_rows(path: Path, sheet: str) -> list[tuple[object, ...]]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[sheet]
        return [tuple(row) for row in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def test_multi_config_runner_builds_main_cli_command(tmp_path: Path) -> None:
    mod = _load_module(REPO_ROOT / "run_configs_tester_batch.py", "tester_batch_tool")
    cmd = mod.build_command(
        sys.executable,
        tmp_path / "data.csv",
        tmp_path / "cfg.yaml",
        tmp_path / "out.xlsx",
    )

    assert cmd[:3] == [sys.executable, "-m", "supertrend_optimizer.cli.tester"]
    assert "--csv" in cmd
    assert "--config" in cmd
    assert "--out" in cmd


def test_benchmark_harness_profiles_to_excel_without_export_api_changes() -> None:
    src = (REPO_ROOT / "benchmark_tester_fast_export.py").read_text(encoding="utf-8")

    assert "pd.DataFrame.to_excel = profiled" in src
    assert "summary_100_matches_baseline" in src
    assert "metrics_100_matches_baseline" in src
    assert "export_tester_results(" in src


def test_fast_mode_main_cli_and_batch_legacy_numeric_parity(tmp_path: Path) -> None:
    csv_path = tmp_path / "data.csv"
    cfg_path = tmp_path / "config_tester.yaml"
    main_dir = tmp_path / "main"
    batch_dir = tmp_path / "batch"
    main_dir.mkdir()
    batch_dir.mkdir()
    _write_tiny_ohlc(csv_path)
    _write_fast_config(cfg_path)

    main_requested = main_dir / "main.xlsx"
    main_proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "supertrend_optimizer.cli.tester",
            "--csv",
            str(csv_path),
            "--config",
            str(cfg_path),
            "--out",
            str(main_requested),
        ],
        cwd=REPO_ROOT,
        env=_env(),
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert main_proc.returncode == 0, main_proc.stdout + main_proc.stderr

    batch_proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "donor TESTER" / "run_batch_tester.py"),
            "--csv",
            str(csv_path),
            "--config",
            str(cfg_path),
            "--output-dir",
            str(batch_dir),
        ],
        cwd=REPO_ROOT,
        env=_env(),
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert batch_proc.returncode == 0, batch_proc.stdout + batch_proc.stderr

    main_xlsx = _latest_xlsx(main_dir)
    legacy_outputs = sorted(batch_dir.glob("*legacy*.xlsx"), key=lambda p: p.stat().st_mtime)
    assert legacy_outputs, f"No legacy XLSX files in {batch_dir}"
    batch_xlsx = legacy_outputs[-1]

    assert _sheet_rows(main_xlsx, "Summary") == _sheet_rows(batch_xlsx, "Summary")
    assert _sheet_rows(main_xlsx, "Metrics_100") == _sheet_rows(
        batch_xlsx, "Metrics_100"
    )
