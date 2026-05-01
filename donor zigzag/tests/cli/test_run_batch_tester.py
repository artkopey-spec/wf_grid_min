"""Integration tests for run_batch_tester.py (equal_blocks + legacy)."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


def _write_synthetic_csv(path: Path, n: int = 500) -> None:
    rng = np.random.default_rng(7)
    close = 100.0 + np.cumsum(rng.normal(0, 1, n))
    close = np.clip(close, 1.0, None)
    open_ = close * (1 + rng.normal(0, 0.005, n))
    open_ = np.clip(open_, 1.0, None)
    base_high = np.maximum(open_, close)
    base_low = np.minimum(open_, close)
    high = base_high * (1 + rng.uniform(0.001, 0.01, n))
    low = base_low * (1 - rng.uniform(0.001, 0.01, n))
    low = np.clip(low, 0.01, None)
    index = pd.date_range("2019-01-01", periods=n, freq="D")
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close}, index=index
    )
    df.to_csv(path)


@pytest.fixture
def batch_config_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "batch_cfg.yaml"
    p.write_text(
        """
supertrend:
  atr_period: 10
  multiplier: 2.0
trade_mode: long
commission: 0.0
warmup_period: 20
warmup_period_auto: false
min_trades_required: 1
periods_per_year: 252
segmentation:
  mode: equal_blocks
  n_parts: 3
export:
  false_start_max_bars: 5
""",
        encoding="utf-8",
    )
    return p


def test_run_batch_tester_produces_two_workbooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, batch_config_yaml: Path
):
    """Batch launcher runs equal_blocks then legacy; both Excel files exist."""
    csv_path = tmp_path / "data.csv"
    _write_synthetic_csv(csv_path, n=500)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_batch_tester",
            "--csv",
            str(csv_path),
            "--config",
            str(batch_config_yaml),
            "--output-dir",
            str(tmp_path),
        ],
    )

    import run_batch_tester

    run_batch_tester.main()

    eqblk = list(tmp_path.glob("test_result_equal_blocks_eqblk_*.xlsx"))
    legacy = list(tmp_path.glob("test_result_legacy_*.xlsx"))
    assert len(eqblk) == 1, f"expected one equal_blocks export, got {eqblk}"
    assert len(legacy) == 1, f"expected one legacy export, got {legacy}"


@pytest.fixture
def batch_config_yaml_zigzag(tmp_path: Path) -> Path:
    """
    Minimal `filters.mode: zigzag` config for the batch launcher.  Used by
    the regression test that guards the legacy-segmentation call-site of
    ``build_signal_events``.  The zz block mimics the RFC v3.1 §6.5
    default (no explicit ``readiness:`` → auto-migrates to A=on / B=off
    via cli/tester with a DeprecationWarning).
    """
    p = tmp_path / "batch_cfg_zz.yaml"
    p.write_text(
        """
supertrend:
  atr_period: 10
  multiplier: 2.0
trade_mode: long
commission: 0.0
warmup_period: 20
warmup_period_auto: false
min_trades_required: 1
periods_per_year: 252
segmentation:
  mode: equal_blocks
  n_parts: 3
export:
  false_start_max_bars: 5
filters:
  mode: zigzag
  zigzag:
    reversal_threshold: 0.01
    min_legs_global: 10
    q_strong: 0.80
    k_local: 3
    entry_side: counter_trend
    arm_timeout_bars_since_extreme: 12
    arm_timeout_bars_hard: 48
""",
        encoding="utf-8",
    )
    return p


def test_run_batch_tester_legacy_path_with_zigzag_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    batch_config_yaml_zigzag: Path,
):
    """
    Regression: when ``filters.mode == 'zigzag'``, the legacy segmentation
    branch of run_batch_tester.py must pass ``filter_diagnostics`` to
    ``build_signal_events`` — otherwise signal_events.py §3.0 contract
    raises ``ValueError: build_signal_events: mode='zigzag' requires
    filter_diagnostics with zz_* keys``.

    Guards the fix that mirrors the canonical call-site in cli/tester.py:
    kwargs include ``filter_diagnostics=_fd0_batch``.  Before the fix
    this test fails at the [2/2] legacy step with SystemExit(1).
    """
    csv_path = tmp_path / "data.csv"
    _write_synthetic_csv(csv_path, n=500)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_batch_tester",
            "--csv",
            str(csv_path),
            "--config",
            str(batch_config_yaml_zigzag),
            "--output-dir",
            str(tmp_path),
        ],
    )

    import run_batch_tester

    run_batch_tester.main()

    eqblk = list(tmp_path.glob("test_result_equal_blocks_eqblk_*.xlsx"))
    legacy = list(tmp_path.glob("test_result_legacy_*.xlsx"))
    assert len(eqblk) == 1, (
        f"equal_blocks xlsx missing — the [1/2] branch failed; got {eqblk}"
    )
    assert len(legacy) == 1, (
        f"legacy xlsx missing — the [2/2] branch failed, likely missing "
        f"`filter_diagnostics` kwarg in build_signal_events call-site; "
        f"got {legacy}"
    )

    # Both files must open cleanly and carry the canonical ZZ sheets.
    for xlsx in (eqblk[0], legacy[0]):
        xf = pd.ExcelFile(xlsx)
        assert "Legs" in xf.sheet_names, (
            f"{xlsx.name}: Legs sheet missing (expected for zz mode)"
        )
        assert "filters_summary" in xf.sheet_names, (
            f"{xlsx.name}: filters_summary sheet missing"
        )
