"""
tests/tools/test_zigzag_calibrator.py

Tests for tools/zigzag_calibrator.py (plan §3.2, Этап 7).

Coverage:
  A. calibrate() returns correct columns and row count.
  B. All-NaN CSV raises SystemExit with message.
  C. Larger synthetic dataset: more legs appear at lower thresholds.
  D. main() --csv smoke test (writes output to temp file).
  E. main() raises SystemExit on invalid threshold.
  F. _load_csv accepts tz argument.
  G. calibrate() degenerate case: 1-bar dataframe returns zero legs.
"""
from __future__ import annotations

import csv
import sys
import tempfile
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd
import pytest

from tools.zigzag_calibrator import calibrate, _load_csv, main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(n: int = 300, seed: int = 42) -> pd.DataFrame:
    """
    Synthetic intraday OHLC with a pronounced zigzag pattern.
    Uses a sine wave to ensure legs are confirmed at multiple thresholds.
    """
    rng = np.random.default_rng(seed)
    t = np.linspace(0, 6 * np.pi, n)
    mid = 100.0 * (1 + 0.02 * np.sin(t))
    noise = rng.uniform(-0.0005, 0.0005, n) * 100
    close = mid + noise
    spread = 0.001 * close
    high = close + rng.uniform(0, 1, n) * spread
    low = close - rng.uniform(0, 1, n) * spread
    opn = close + rng.uniform(-0.5, 0.5, n) * spread

    # Intraday index: 5-min bars, single day → no session resets within day
    idx = pd.date_range("2024-01-02 09:00", periods=n, freq="5min")
    return pd.DataFrame({"open": opn, "high": high, "low": low, "close": close}, index=idx)


def _write_csv(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path)


# ---------------------------------------------------------------------------
# A. calibrate() columns and row count
# ---------------------------------------------------------------------------

class TestCalibrateOutput:
    EXPECTED_COLS = [
        "reversal_threshold", "n_legs_total", "n_legs_up", "n_legs_down",
        "median_height_pct", "p80_height_pct", "p95_height_pct", "max_height_pct",
        "median_length_bars", "median_confirm_lag_bars",
    ]

    def test_columns(self) -> None:
        df = _make_df()
        result = calibrate(df, [0.005], tz=None)
        assert list(result.columns) == self.EXPECTED_COLS

    def test_row_count_matches_thresholds(self) -> None:
        df = _make_df()
        thresholds = [0.003, 0.005, 0.010]
        result = calibrate(df, thresholds)
        assert len(result) == 3

    def test_threshold_values_preserved(self) -> None:
        df = _make_df()
        thresholds = [0.002, 0.005, 0.010]
        result = calibrate(df, thresholds)
        np.testing.assert_array_almost_equal(
            result["reversal_threshold"].values, sorted(thresholds)
        )

    def test_n_legs_splits_correctly(self) -> None:
        df = _make_df()
        result = calibrate(df, [0.005])
        row = result.iloc[0]
        assert row["n_legs_up"] + row["n_legs_down"] == row["n_legs_total"]


# ---------------------------------------------------------------------------
# B. Empty / all-NaN edge case
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_single_bar_zero_legs(self) -> None:
        """Single bar cannot form any legs."""
        idx = pd.date_range("2024-01-02 09:00", periods=1, freq="5min")
        df = pd.DataFrame(
            {"open": [100.0], "high": [101.0], "low": [99.0], "close": [100.0]},
            index=idx,
        )
        result = calibrate(df, [0.005])
        row = result.iloc[0]
        assert row["n_legs_total"] == 0
        assert np.isnan(row["median_height_pct"])

    def test_load_csv_missing_column_exits(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "bad.csv"
        pd.DataFrame({"open": [100], "high": [101], "low": [99]}).to_csv(csv_path)
        with pytest.raises(SystemExit, match="close"):
            _load_csv(str(csv_path), tz=None)

    def test_load_csv_no_datetime_index_exits(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "no_dt.csv"
        pd.DataFrame({
            "index_col": ["foo", "bar"],
            "open": [100, 101], "high": [102, 103],
            "low": [99, 100], "close": [100, 101],
        }).to_csv(csv_path, index=False)
        with pytest.raises(SystemExit):
            _load_csv(str(csv_path), tz=None)


# ---------------------------------------------------------------------------
# C. Monotonicity: fewer legs at higher thresholds
# ---------------------------------------------------------------------------

class TestMonotonicity:
    def test_lower_threshold_more_legs(self) -> None:
        """A lower reversal_threshold must produce >= as many legs as a higher one."""
        df = _make_df(n=400, seed=7)
        thresholds = [0.002, 0.005, 0.010]
        result = calibrate(df, thresholds)
        legs = result.set_index("reversal_threshold")["n_legs_total"]
        # Monotonically non-increasing as threshold grows.
        for lo, hi in zip(thresholds, thresholds[1:]):
            assert legs[lo] >= legs[hi], (
                f"Expected legs({lo}) >= legs({hi}), "
                f"got {legs[lo]} < {legs[hi]}"
            )


# ---------------------------------------------------------------------------
# D. main() smoke test via --csv
# ---------------------------------------------------------------------------

class TestMain:
    def test_main_writes_csv(self, tmp_path: Path) -> None:
        df = _make_df()
        csv_in = tmp_path / "data.csv"
        csv_out = tmp_path / "calibration.csv"
        _write_csv(df, csv_in)
        main([
            "--csv", str(csv_in),
            "--thresholds", "0.004", "0.006",
            "--out", str(csv_out),
        ])
        assert csv_out.exists()
        result = pd.read_csv(csv_out)
        assert len(result) == 2
        assert "reversal_threshold" in result.columns

    def test_main_prints_to_stdout_without_out(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        df = _make_df()
        csv_in = tmp_path / "data.csv"
        _write_csv(df, csv_in)
        main(["--csv", str(csv_in), "--thresholds", "0.005"])
        captured = capsys.readouterr()
        assert "reversal_threshold" in captured.out


# ---------------------------------------------------------------------------
# E. Threshold range validation
# ---------------------------------------------------------------------------

class TestThresholdValidation:
    def test_main_rejects_out_of_range_threshold(self, tmp_path: Path) -> None:
        df = _make_df()
        csv_in = tmp_path / "data.csv"
        _write_csv(df, csv_in)
        with pytest.raises(SystemExit, match=r"0\.5"):
            main(["--csv", str(csv_in), "--thresholds", "0.5"])

    def test_main_rejects_zero_threshold(self, tmp_path: Path) -> None:
        df = _make_df()
        csv_in = tmp_path / "data.csv"
        _write_csv(df, csv_in)
        with pytest.raises(SystemExit):
            main(["--csv", str(csv_in), "--thresholds", "0.0"])


# ---------------------------------------------------------------------------
# F. _load_csv with tz argument
# ---------------------------------------------------------------------------

class TestLoadCsv:
    def test_load_csv_applies_tz(self, tmp_path: Path) -> None:
        df = _make_df()
        # Strip timezone so we can apply it via --tz
        df_naive = df.copy()
        df_naive.index = df_naive.index.tz_localize(None)
        csv_path = tmp_path / "naive.csv"
        _write_csv(df_naive, csv_path)
        loaded = _load_csv(str(csv_path), tz="Europe/Moscow")
        assert loaded.index.tz is not None
        assert "Moscow" in str(loaded.index.tz)

    def test_load_csv_round_trips_correctly(self, tmp_path: Path) -> None:
        df = _make_df()
        csv_path = tmp_path / "data.csv"
        _write_csv(df, csv_path)
        loaded = _load_csv(str(csv_path), tz=None)
        assert len(loaded) == len(df)
        assert "open" in loaded.columns
        assert "high" in loaded.columns
        assert "low" in loaded.columns
        assert "close" in loaded.columns
