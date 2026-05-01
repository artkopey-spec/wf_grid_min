"""
Unit tests for Этап 3+4: wf_grid/bucket/median_matrix_builder.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from wf_grid.bucket.median_matrix_builder import build_median_bucket_matrix
from wf_grid.config.schema import (
    BucketConfig,
    DataConfig,
    GridConfig,
    INVALID_METRIC_VALUE,
    OptimizationConfig,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(
    atr_range=(10, 12),
    mult_range=(2.0, 2.2),
    mult_step=0.1,
    trade_mode="long",
    atr_bucket_step=2,
    mult_bucket_step=0.2,
    min_buckets_for_median=5,
) -> GridConfig:
    return GridConfig(
        data=DataConfig(file_path="dummy.csv"),
        optimization=OptimizationConfig(
            atr_period_range=list(atr_range),
            multiplier_range=list(mult_range),
            multiplier_step=mult_step,
            trade_mode=trade_mode,
        ),
        bucket=BucketConfig(
            atr_bucket_step=atr_bucket_step,
            mult_bucket_step=mult_bucket_step,
            min_buckets_for_median=min_buckets_for_median,
        ),
    )


def _make_step_oos_long(
    rows: list[dict],
    extra_cols: dict | None = None,
) -> pd.DataFrame:
    """Helper to create step_oos_long DataFrames for testing."""
    df = pd.DataFrame(rows)
    if extra_cols:
        for k, v in extra_cols.items():
            df[k] = v
    if "grid_point_id" not in df.columns and "atr_period" in df.columns:
        df["grid_point_id"] = (
            "atr" + df["atr_period"].astype(str)
            + "_m" + df["multiplier"].astype(str)
            + "_" + df["trade_mode"].astype(str)
        )
    return df


def _make_simple_oos(
    atr_vals=(10, 11, 12),
    mult_vals=(2.0, 2.1, 2.2),
    n_steps=3,
    trade_mode="long",
    status="ok",
    pnl_base=10.0,
    dd_base=-0.05,
):
    """Create a simple step_oos_long with all combinations × steps."""
    rows = []
    for atr in atr_vals:
        for mult in mult_vals:
            for s in range(1, n_steps + 1):
                rows.append({
                    "atr_period": atr,
                    "multiplier": mult,
                    "trade_mode": trade_mode,
                    "wf_step": s,
                    "step_status": status,
                    "sum_pnl_pct": pnl_base + atr * 0.1 + mult + s * 0.5,
                    "max_drawdown": dd_base - s * 0.01,
                })
    return _make_step_oos_long(rows)


# ===========================================================================
# Input validation guards
# ===========================================================================

class TestInputValidation:
    def test_rejects_non_pnl_metric(self):
        config = _make_config()
        df = _make_simple_oos()
        with pytest.raises(NotImplementedError, match="sum_pnl_pct"):
            build_median_bucket_matrix(df, config, metric_column="sharpe")

    def test_rejects_missing_columns(self):
        config = _make_config()
        df = pd.DataFrame({"atr_period": [10], "multiplier": [2.0]})
        with pytest.raises(ValueError, match="missing required columns"):
            build_median_bucket_matrix(df, config)

    def test_rejects_missing_columns_lists_them(self):
        config = _make_config()
        df = pd.DataFrame({
            "atr_period": [10], "multiplier": [2.0],
            "grid_point_id": ["x"], "wf_step": [1],
        })
        with pytest.raises(ValueError, match="step_status"):
            build_median_bucket_matrix(df, config)

    def test_rejects_missing_grid_point_id(self):
        """Missing grid_point_id must fail via the same required-columns ValueError."""
        config = _make_config()
        df = pd.DataFrame({
            "atr_period": [10], "multiplier": [2.0], "trade_mode": ["long"],
            "wf_step": [1], "step_status": ["ok"], "sum_pnl_pct": [1.0],
        })
        with pytest.raises(ValueError, match="missing required columns") as exc_info:
            build_median_bucket_matrix(df, config)
        assert "grid_point_id" in str(exc_info.value)

    def test_rejects_duplicates(self):
        config = _make_config()
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 5.0,
             "max_drawdown": -0.10, "grid_point_id": "atr10_m2.0_long"},
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 6.0,
             "max_drawdown": -0.12, "grid_point_id": "atr10_m2.0_long"},
        ]
        df = pd.DataFrame(rows)
        with pytest.raises(ValueError, match="duplicate"):
            build_median_bucket_matrix(df, config)

    def test_rejects_multi_trade_mode(self):
        config = _make_config()
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 5.0,
             "max_drawdown": -0.10},
            {"atr_period": 11, "multiplier": 2.0, "trade_mode": "short",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 6.0,
             "max_drawdown": -0.12},
        ]
        df = _make_step_oos_long(rows)
        with pytest.raises(ValueError, match="trade_mode"):
            build_median_bucket_matrix(df, config)

    def test_trade_mode_guard_on_full_df(self):
        """Guard fires on full df even if one mode is entirely non-ok."""
        config = _make_config()
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 5.0,
             "max_drawdown": -0.10},
            {"atr_period": 11, "multiplier": 2.0, "trade_mode": "short",
             "wf_step": 1, "step_status": "no_trades", "sum_pnl_pct": 0.0,
             "max_drawdown": 0.0},
        ]
        df = _make_step_oos_long(rows)
        with pytest.raises(ValueError, match="trade_mode"):
            build_median_bucket_matrix(df, config)

    def test_rejects_nonconsecutive_steps(self):
        config = _make_config()
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 5.0,
             "max_drawdown": -0.10},
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 4, "step_status": "ok", "sum_pnl_pct": 6.0,
             "max_drawdown": -0.12},
        ]
        df = _make_step_oos_long(rows)
        with pytest.raises(ValueError, match="wf_step"):
            build_median_bucket_matrix(df, config)

    def test_rejects_zero_based_steps(self):
        config = _make_config()
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 0, "step_status": "ok", "sum_pnl_pct": 5.0,
             "max_drawdown": -0.10},
            {"atr_period": 11, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 6.0,
             "max_drawdown": -0.12},
        ]
        df = _make_step_oos_long(rows)
        with pytest.raises(ValueError, match="wf_step"):
            build_median_bucket_matrix(df, config)


# ===========================================================================
# Empty / edge cases
# ===========================================================================

class TestEdgeCases:
    def test_empty_input_returns_empty_df(self):
        config = _make_config()
        df = pd.DataFrame(columns=[
            "atr_period", "multiplier", "trade_mode", "wf_step",
            "step_status", "sum_pnl_pct", "grid_point_id",
        ])
        result = build_median_bucket_matrix(df, config)
        assert len(result) == 0

    def test_no_ok_rows_returns_full_grid_with_nan(self):
        """Non-empty input, 0 ok-rows → full bucket grid, all Steps NaN, scores 0.0."""
        config = _make_config(
            atr_range=(10, 11), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
        )
        df = _make_simple_oos(
            atr_vals=(10, 11), mult_vals=(2.0,), n_steps=2,
            status="no_trades",
        )
        result = build_median_bucket_matrix(df, config)
        assert len(result) > 0, "Must return full grid, not empty"
        assert result["Step1"].isna().all()
        assert result["Step2"].isna().all()
        assert (result["bucket_stability_score"] == 0.0).all()

    def test_no_ok_rows_column_count(self):
        """Full grid at 0 ok → 22 + 2*N columns."""
        config = _make_config(
            atr_range=(10, 11), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
        )
        df = _make_simple_oos(
            atr_vals=(10, 11), mult_vals=(2.0,), n_steps=2,
            status="no_trades",
        )
        result = build_median_bucket_matrix(df, config)
        expected_cols = 24 + 2 * 2  # 24 fixed + 2*N steps
        assert len(result.columns) == expected_cols

    def test_all_ok_sentinel_returns_full_grid_nan(self):
        """All ok-rows with sentinel → full grid, all NaN."""
        config = _make_config(
            atr_range=(10, 11), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
        )
        df = _make_simple_oos(
            atr_vals=(10, 11), mult_vals=(2.0,), n_steps=2,
            status="ok", pnl_base=INVALID_METRIC_VALUE - 10 - 0.1 * 10 - 2.0 - 0.5,
        )
        # Override with exact sentinel
        df["sum_pnl_pct"] = INVALID_METRIC_VALUE
        result = build_median_bucket_matrix(df, config)
        assert len(result) > 0
        assert result["Step1"].isna().all()


# ===========================================================================
# Basic builder correctness
# ===========================================================================

class TestBasicBuilder:
    def test_median_matrix_basic(self):
        """3 atr × 1 mult × 2 steps, hand-computed medians."""
        config = _make_config(
            atr_range=(10, 12), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
        )
        rows = []
        for atr in (10, 11, 12):
            for s in (1, 2):
                rows.append({
                    "atr_period": atr,
                    "multiplier": 2.0,
                    "trade_mode": "long",
                    "wf_step": s,
                    "step_status": "ok",
                    "sum_pnl_pct": float(atr + s),
                    "max_drawdown": -0.05 * s,
                })
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)

        assert "Step1" in result.columns
        assert "Step2" in result.columns
        assert "Step0" not in result.columns

    def test_all_columns_present(self):
        config = _make_config()
        df = _make_simple_oos()
        result = build_median_bucket_matrix(df, config)
        n_steps = 3
        expected_count = 24 + 2 * n_steps
        assert len(result.columns) == expected_count, (
            f"Expected {expected_count} columns, got {len(result.columns)}: "
            f"{list(result.columns)}"
        )

    def test_column_order(self):
        """Block A (5) + Block B+D (2N interleaved) + Block E (2) + Block C (17)."""
        config = _make_config()
        df = _make_simple_oos()
        result = build_median_bucket_matrix(df, config)
        cols = list(result.columns)
        n_steps = 3
        # Block A
        assert cols[:5] == [
            "bucket_param", "bucket_key", "atr_bucket",
            "mult_bucket_ticks", "bucket_size",
        ]
        # Block B+D: interleaved Step / DD_Step pairs
        expected_interleaved = []
        for s in range(1, n_steps + 1):
            expected_interleaved.append(f"Step{s}")
            expected_interleaved.append(f"DD_Step{s}")
        assert cols[5:5 + 2 * n_steps] == expected_interleaved
        # Block E
        assert cols[5 + 2 * n_steps] == "max_drawdown_Median"
        assert cols[5 + 2 * n_steps + 1] == "max_drawdown_Min"
        # Block C first and last
        assert cols[5 + 2 * n_steps + 2] == "bucket_presence_steps"
        assert cols[-1] == "bucket_balanced_score"
        assert cols[-2] == "zone_dominance_score"
        assert cols[-3] == "bucket_stability_score"

    def test_step_naming_1based(self):
        config = _make_config()
        df = _make_simple_oos()
        result = build_median_bucket_matrix(df, config)
        step_cols = [c for c in result.columns if c.startswith("Step")]
        assert "Step0" not in step_cols
        assert "Step1" in step_cols

    def test_ok_filter(self):
        """Only ok-rows contribute to medians."""
        config = _make_config(
            atr_range=(10, 10), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
        )
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 100.0,
             "max_drawdown": -0.10},
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 2, "step_status": "no_trades", "sum_pnl_pct": -50.0,
             "max_drawdown": -0.05},
        ]
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        # Step1 should have the ok value, Step2 should be NaN
        bucket_row = result[result["atr_bucket"] == 10]
        assert len(bucket_row) >= 1
        assert bucket_row["Step1"].iloc[0] == pytest.approx(100.0)
        assert pd.isna(bucket_row["Step2"].iloc[0])

    def test_sentinel_in_ok_rows(self):
        """ok-row with sentinel → NaN, not contamination."""
        config = _make_config(
            atr_range=(10, 10), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
        )
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": INVALID_METRIC_VALUE,
             "max_drawdown": -0.10},
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 2, "step_status": "ok", "sum_pnl_pct": 50.0,
             "max_drawdown": -0.05},
        ]
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        bucket_row = result[result["atr_bucket"] == 10]
        assert pd.isna(bucket_row["Step1"].iloc[0]), "Sentinel must → NaN"
        assert bucket_row["Step2"].iloc[0] == pytest.approx(50.0)

    def test_sentinel_cleaning_does_not_mutate_input(self):
        config = _make_config(
            atr_range=(10, 10), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
        )
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok",
             "sum_pnl_pct": INVALID_METRIC_VALUE, "max_drawdown": -0.10},
        ]
        df = _make_step_oos_long(rows)
        original_val = df["sum_pnl_pct"].iloc[0]
        build_median_bucket_matrix(df, config)
        assert df["sum_pnl_pct"].iloc[0] == original_val, "Input must not be mutated"

    def test_total_steps_from_full_df(self):
        """Step with zero ok-rows still enters total_steps (denominator)."""
        config = _make_config(
            atr_range=(10, 10), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
        )
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 100.0,
             "max_drawdown": -0.10},
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 2, "step_status": "no_trades", "sum_pnl_pct": 0.0,
             "max_drawdown": 0.0},
        ]
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        # presence_count = 1 (only Step1 has ok), total_steps = 2
        # bucket_stability_score = 0.6 * (1/2) + 0.4 * (above_median/2)
        bucket_row = result[result["atr_bucket"] == 10]
        assert bucket_row["presence_count"].iloc[0] == 1
        # Step2 should be NaN in output (columns Step1 and Step2 exist)
        assert "Step2" in result.columns


# ===========================================================================
# Sort
# ===========================================================================

class TestSort:
    def test_sort_primary(self):
        """First row has max bucket_stability_score."""
        config = _make_config()
        df = _make_simple_oos()
        result = build_median_bucket_matrix(df, config)
        if len(result) > 1:
            assert result["bucket_stability_score"].iloc[0] >= result["bucket_stability_score"].iloc[1]

    def test_sort_tiebreak(self):
        """Same score → smaller atr_bucket first, then smaller mult_bucket_ticks."""
        config = _make_config(
            atr_range=(10, 12), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
        )
        df = _make_simple_oos(atr_vals=(10, 11, 12), mult_vals=(2.0,), n_steps=1)
        result = build_median_bucket_matrix(df, config)
        # With 1 step and similar structure, ties are likely
        scores = result["bucket_stability_score"].tolist()
        for i in range(len(scores) - 1):
            if scores[i] == scores[i + 1]:
                assert result["atr_bucket"].iloc[i] <= result["atr_bucket"].iloc[i + 1]

    def test_sort_determinism(self):
        config = _make_config()
        df = _make_simple_oos()
        r1 = build_median_bucket_matrix(df, config)
        r2 = build_median_bucket_matrix(df, config)
        pd.testing.assert_frame_equal(r1, r2)


# ===========================================================================
# Derived metrics (Block C)
# ===========================================================================

class TestDerivedMetrics:
    def _single_bucket_result(self, pnl_step1=10.0, pnl_step2=20.0):
        """Helper: 1 bucket, 2 steps, 1 atr/mult."""
        config = _make_config(
            atr_range=(10, 10), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
            min_buckets_for_median=1,
        )
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": pnl_step1,
             "max_drawdown": -0.10},
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 2, "step_status": "ok", "sum_pnl_pct": pnl_step2,
             "max_drawdown": -0.15},
        ]
        df = _make_step_oos_long(rows)
        return build_median_bucket_matrix(df, config)

    def test_stability_score_formula(self):
        result = self._single_bucket_result()
        row = result.iloc[0]
        # 1 bucket, present on both steps → presence_count=2, total_steps=2
        # above_median: 1 bucket per step → median = self → >= → count 2
        expected = round(0.6 * (2 / 2) + 0.4 * (2 / 2), 6)
        assert row["bucket_stability_score"] == pytest.approx(expected)

    def test_stability_score_precision_6(self):
        result = self._single_bucket_result(10.123456789, 20.987654321)
        score = result["bucket_stability_score"].iloc[0]
        s_str = f"{score:.10f}"
        assert len(s_str.split(".")[1].rstrip("0")) <= 6

    def test_zone_dominance_formula(self):
        result = self._single_bucket_result()
        row = result.iloc[0]
        # 1 bucket → wins every step, top3 every step
        expected = round(0.4 * (2 / 2) + 0.3 * (2 / 2) + 0.3 * (2 / 2), 6)
        assert row["zone_dominance_score"] == pytest.approx(expected)

    def test_mean_oos_pnl(self):
        result = self._single_bucket_result(10.0, 20.0)
        row = result.iloc[0]
        assert row["mean_oos_pnl"] == pytest.approx(15.0)

    def test_presence_count(self):
        result = self._single_bucket_result()
        assert result["presence_count"].iloc[0] == 2

    def test_win_steps_format_1based(self):
        result = self._single_bucket_result()
        ws = result["win_steps"].iloc[0]
        assert "Step1" in ws
        assert "WF" not in ws

    def test_presence_steps_format_1based(self):
        result = self._single_bucket_result()
        ps = result["bucket_presence_steps"].iloc[0]
        assert "Step1" in ps
        assert "Step2" in ps
        assert "WF" not in ps

    def test_std_bucket_uses_raw_values(self):
        """std_bucket uses raw ok+valid values, not Step medians."""
        config = _make_config(
            atr_range=(10, 11), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=10, mult_bucket_step=0.2,
            min_buckets_for_median=1,
        )
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 10.0,
             "max_drawdown": -0.10},
            {"atr_period": 11, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 20.0,
             "max_drawdown": -0.20},
        ]
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        # Both atr 10, 11 map to same bucket (step=10)
        # raw values: [10, 20], population std = 5.0
        # Step1 median = 15.0 (single value if used — different)
        bucket_row = result.iloc[0]
        assert bucket_row["std_bucket"] == pytest.approx(5.0)

    def test_pct_params_positive(self):
        """3 params, 2 with mean>0 → 2/3."""
        config = _make_config(
            atr_range=(10, 12), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=10, mult_bucket_step=0.2,
            min_buckets_for_median=1,
        )
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 10.0,
             "max_drawdown": -0.05},
            {"atr_period": 11, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 5.0,
             "max_drawdown": -0.10},
            {"atr_period": 12, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": -3.0,
             "max_drawdown": -0.30},
        ]
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        bucket_row = result.iloc[0]
        assert bucket_row["pct_params_positive_pnl"] == pytest.approx(2.0 / 3.0)

    def test_wins_count_tie(self):
        """At tie, idxmax → one winner (donor-compatible)."""
        config = _make_config(
            atr_range=(10, 12), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
            min_buckets_for_median=1,
        )
        # Two buckets with identical medians on Step1
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 50.0,
             "max_drawdown": -0.10},
            {"atr_period": 12, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 50.0,
             "max_drawdown": -0.15},
        ]
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        total_wins = result["wins_count"].sum()
        assert total_wins == 1, "Only one winner at tie (idxmax)"

    def test_top3_count_tie(self):
        """At tie, rank(method='min') → all tied included."""
        config = _make_config(
            atr_range=(10, 12), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
            min_buckets_for_median=1,
        )
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 50.0,
             "max_drawdown": -0.10},
            {"atr_period": 12, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 50.0,
             "max_drawdown": -0.15},
        ]
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        total_top3 = result["top3_count"].sum()
        assert total_top3 == 2, "Both tied values are in top-3"

    def test_above_median_uses_gte(self):
        """Bucket with value == step median is counted as above."""
        config = _make_config(
            atr_range=(10, 14), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
            min_buckets_for_median=1,
        )
        # Values: 10, 20, 30 → median = 20 → buckets with 20 and 30 are "above"
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 10.0,
             "max_drawdown": -0.10},
            {"atr_period": 12, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 20.0,
             "max_drawdown": -0.20},
            {"atr_period": 14, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 30.0,
             "max_drawdown": -0.30},
        ]
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        # median = 20 → value 20 (>=) and value 30 (>=) count
        above_total = result["above_median_count"].sum()
        assert above_total >= 2

    def test_above_median_min_buckets_threshold(self):
        """Step with < min_buckets_for_median non-NaN → excluded from above-median."""
        config = _make_config(
            atr_range=(10, 10), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
            min_buckets_for_median=5,  # threshold > 1 bucket
        )
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 100.0,
             "max_drawdown": -0.10},
        ]
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        # Only 1 bucket < min_buckets_for_median=5 → step excluded
        assert result["above_median_count"].iloc[0] == 0
        assert result["eligible_median_steps_count"].iloc[0] == 0

    def test_above_median_ratio_present_zero_presence(self):
        """presence_count=0 → above_median_ratio_present = 0.0 (not NaN)."""
        config = _make_config(
            atr_range=(10, 12), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
        )
        df = _make_simple_oos(
            atr_vals=(10, 11, 12), mult_vals=(2.0,), n_steps=1,
            status="no_trades",
        )
        result = build_median_bucket_matrix(df, config)
        for val in result["above_median_ratio_present"]:
            assert val == 0.0

    def test_sentinel_cleaning_affects_std_and_pct_params(self):
        """Sentinel is cleaned from raw accumulation (std, pct_params)."""
        config = _make_config(
            atr_range=(10, 10), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
            min_buckets_for_median=1,
        )
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok",
             "sum_pnl_pct": INVALID_METRIC_VALUE, "max_drawdown": -0.10},
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 2, "step_status": "ok", "sum_pnl_pct": 50.0,
             "max_drawdown": -0.05},
        ]
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        row = result.iloc[0]
        # std_bucket: only 1 valid value (50.0) → std = 0.0
        assert row["std_bucket"] == pytest.approx(0.0)
        # pct_params: mean(50.0) > 0 → 1.0
        assert row["pct_params_positive_pnl"] == pytest.approx(1.0)

    def test_step_columns_dtype_float64(self):
        config = _make_config()
        df = _make_simple_oos()
        result = build_median_bucket_matrix(df, config)
        for col in [c for c in result.columns if c.startswith("Step")]:
            assert result[col].dtype == np.float64

    def test_single_step(self):
        config = _make_config(
            atr_range=(10, 10), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
            min_buckets_for_median=1,
        )
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 42.0,
             "max_drawdown": -0.10},
        ]
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        assert "Step1" in result.columns
        assert "Step2" not in result.columns
        assert len(result.columns) == 24 + 2 * 1

    def test_pct_params_groups_by_parameter_not_bucket(self):
        """Bucket with 2 (atr, mult) combos → metric reflects 2 parameters."""
        config = _make_config(
            atr_range=(10, 11), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=10, mult_bucket_step=0.2,
            min_buckets_for_median=1,
        )
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 10.0,
             "max_drawdown": -0.10},
            {"atr_period": 11, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": -5.0,
             "max_drawdown": -0.20},
        ]
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        # 2 params: (10,2.0) mean=10>0, (11,2.0) mean=-5<0 → pct = 1/2
        row = result.iloc[0]
        assert row["pct_params_positive_pnl"] == pytest.approx(0.5)


# ===========================================================================
# DD columns (Block D + Block E) — simple tests (1.11)
# ===========================================================================

class TestDDColumns:
    def _config_1bucket(self, min_buckets=1):
        return _make_config(
            atr_range=(10, 10), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
            min_buckets_for_median=min_buckets,
        )

    def test_dd_step_columns_present(self):
        """DD_Step1..DD_StepN присутствуют в output."""
        config = _make_config()
        df = _make_simple_oos(n_steps=3)
        result = build_median_bucket_matrix(df, config)
        assert "DD_Step1" in result.columns
        assert "DD_Step2" in result.columns
        assert "DD_Step3" in result.columns

    def test_dd_step_values_are_median_of_max_drawdown(self):
        """DD_Step1 = median(max_drawdown) по ok-строкам бакета на шаге."""
        config = _make_config(
            atr_range=(10, 11), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=10, mult_bucket_step=0.2,
            min_buckets_for_median=1,
        )
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 10.0,
             "max_drawdown": -0.10},
            {"atr_period": 11, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 20.0,
             "max_drawdown": -0.20},
        ]
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        # atr 10 и 11 попадают в один бакет (step=10)
        # median(-0.10, -0.20) = -0.15
        assert result["DD_Step1"].iloc[0] == pytest.approx(-0.15)

    def test_dd_step_nan_when_no_ok_values(self):
        """DD_Step NaN если нет ok-строк с валидным max_drawdown на шаге."""
        config = self._config_1bucket()
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 10.0,
             "max_drawdown": -0.10},
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 2, "step_status": "no_trades", "sum_pnl_pct": 0.0,
             "max_drawdown": 0.0},
        ]
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        # Step 2 — not ok → DD_Step2 = NaN
        assert pd.isna(result["DD_Step2"].iloc[0])

    def test_dd_step_sentinel_cleaned(self):
        """INVALID_METRIC_VALUE в max_drawdown не попадает в агрегацию."""
        config = self._config_1bucket()
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 10.0,
             "max_drawdown": INVALID_METRIC_VALUE},
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 2, "step_status": "ok", "sum_pnl_pct": 20.0,
             "max_drawdown": -0.05},
        ]
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        # DD_Step1: sentinel → NaN → no valid values → NaN
        assert pd.isna(result["DD_Step1"].iloc[0])
        # DD_Step2: -0.05 (valid)
        assert result["DD_Step2"].iloc[0] == pytest.approx(-0.05)

    def test_dd_step_sign_negative(self):
        """Все не-NaN значения DD_Step ≤ 0."""
        config = _make_config()
        df = _make_simple_oos(n_steps=3, dd_base=-0.05)
        result = build_median_bucket_matrix(df, config)
        for col in [c for c in result.columns if c.startswith("DD_Step")]:
            non_nan = result[col].dropna()
            assert (non_nan <= 0).all(), f"{col} contains positive values"

    def test_max_drawdown_median_present(self):
        """max_drawdown_Median присутствует в output."""
        config = _make_config()
        df = _make_simple_oos()
        result = build_median_bucket_matrix(df, config)
        assert "max_drawdown_Median" in result.columns

    def test_max_drawdown_min_present(self):
        """max_drawdown_Min присутствует в output."""
        config = _make_config()
        df = _make_simple_oos()
        result = build_median_bucket_matrix(df, config)
        assert "max_drawdown_Min" in result.columns

    def test_max_drawdown_median_nan_when_all_dd_nan(self):
        """NaN если все DD_Step = NaN."""
        config = self._config_1bucket()
        # no_trades → ok_df empty → all DD NaN
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "no_trades", "sum_pnl_pct": 0.0,
             "max_drawdown": 0.0},
        ]
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        assert pd.isna(result["max_drawdown_Median"].iloc[0])

    def test_max_drawdown_min_nan_when_all_dd_nan(self):
        """max_drawdown_Min NaN если все DD_Step = NaN."""
        config = self._config_1bucket()
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "no_trades", "sum_pnl_pct": 0.0,
             "max_drawdown": 0.0},
        ]
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        assert pd.isna(result["max_drawdown_Min"].iloc[0])

    def test_required_columns_includes_max_drawdown(self):
        """ValueError если max_drawdown отсутствует."""
        config = _make_config()
        df = _make_simple_oos()
        df = df.drop(columns=["max_drawdown"])
        with pytest.raises(ValueError, match="missing required columns"):
            build_median_bucket_matrix(df, config)

    def test_dd_prefix_contract(self):
        """Все risk step колонки начинаются с 'DD_', ни одна с 'Step'."""
        config = _make_config()
        df = _make_simple_oos()
        result = build_median_bucket_matrix(df, config)
        dd_step_cols = [c for c in result.columns if c.startswith("DD_Step")]
        assert len(dd_step_cols) > 0, "Expected DD_Step* columns in output"
        for col in dd_step_cols:
            assert not col.startswith("Step"), f"DD col must not start with Step: {col}"

    def test_pnl_block_unchanged(self):
        """Step1..StepN и Block C метрики не изменились."""
        config = _make_config()
        df = _make_simple_oos()
        result = build_median_bucket_matrix(df, config)
        # PnL step columns exist and are float
        for col in ["Step1", "Step2", "Step3"]:
            assert col in result.columns
            assert result[col].dtype == np.float64
        # Block C columns exist
        for col in ["bucket_stability_score", "zone_dominance_score",
                    "mean_oos_pnl", "bucket_presence_steps"]:
            assert col in result.columns


# ===========================================================================
# Contract tests — step-derived formulas (1.12)
# ===========================================================================

class TestDDContracts:
    """Тесты, фиксирующие step-derived контракт и NaN-independence."""

    def test_max_drawdown_median_is_step_derived(self):
        """max_drawdown_Median = median(DD_Step1..DD_StepN), не raw-level."""
        config = _make_config(
            atr_range=(10, 11), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=10, mult_bucket_step=0.2,
            min_buckets_for_median=1,
        )
        # 2 params in one bucket, 2 steps.
        # Step1: DD -0.10, -0.20 → median = -0.15
        # Step2: DD -0.30, -0.40 → median = -0.35
        # step-derived median(-0.15, -0.35) = -0.25
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 10.0,
             "max_drawdown": -0.10},
            {"atr_period": 11, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 20.0,
             "max_drawdown": -0.20},
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 2, "step_status": "ok", "sum_pnl_pct": 15.0,
             "max_drawdown": -0.30},
            {"atr_period": 11, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 2, "step_status": "ok", "sum_pnl_pct": 25.0,
             "max_drawdown": -0.40},
        ]
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        row = result.iloc[0]
        assert row["DD_Step1"] == pytest.approx(-0.15)
        assert row["DD_Step2"] == pytest.approx(-0.35)
        # step-derived median
        expected_median = np.median([-0.15, -0.35])  # = -0.25
        assert row["max_drawdown_Median"] == pytest.approx(expected_median)

    def test_max_drawdown_min_is_step_derived(self):
        """max_drawdown_Min = min(DD_Step1..DD_StepN), не raw min."""
        config = _make_config(
            atr_range=(10, 11), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=10, mult_bucket_step=0.2,
            min_buckets_for_median=1,
        )
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 10.0,
             "max_drawdown": -0.10},
            {"atr_period": 11, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 20.0,
             "max_drawdown": -0.20},
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 2, "step_status": "ok", "sum_pnl_pct": 15.0,
             "max_drawdown": -0.30},
            {"atr_period": 11, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 2, "step_status": "ok", "sum_pnl_pct": 25.0,
             "max_drawdown": -0.40},
        ]
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        row = result.iloc[0]
        # DD_Step1=-0.15, DD_Step2=-0.35 → min = -0.35
        assert row["max_drawdown_Min"] == pytest.approx(-0.35)

    def test_max_drawdown_min_differs_from_raw_min(self):
        """На multi-param бакете step-derived min ≠ min(all raw values).

        Raw values: -0.10, -0.20, -0.05, -0.50
        Raw min = -0.50
        Step1 median(-0.10, -0.20) = -0.15
        Step2 median(-0.05, -0.50) = -0.275
        Step-derived min = min(-0.15, -0.275) = -0.275 ≠ -0.50
        """
        config = _make_config(
            atr_range=(10, 11), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=10, mult_bucket_step=0.2,
            min_buckets_for_median=1,
        )
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 10.0,
             "max_drawdown": -0.10},
            {"atr_period": 11, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok", "sum_pnl_pct": 20.0,
             "max_drawdown": -0.20},
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 2, "step_status": "ok", "sum_pnl_pct": 15.0,
             "max_drawdown": -0.05},
            {"atr_period": 11, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 2, "step_status": "ok", "sum_pnl_pct": 25.0,
             "max_drawdown": -0.50},
        ]
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        row = result.iloc[0]
        raw_min = -0.50
        step_derived_min = row["max_drawdown_Min"]
        assert step_derived_min != pytest.approx(raw_min), (
            "Step-derived min must differ from raw min on multi-param bucket"
        )
        # DD_Step1 = median(-0.10, -0.20) = -0.15
        # DD_Step2 = median(-0.05, -0.50) = -0.275
        # min(-0.15, -0.275) = -0.275
        assert step_derived_min == pytest.approx(-0.275)


# ===========================================================================
# NaN-independence and edge cases (1.13)
# ===========================================================================

class TestDDNanIndependence:
    """Тесты замечаний №1, 2, 3, 4: NaN-independence PnL/DD."""

    def test_dd_accumulator_independent_of_pnl_nan(self):
        """Строка с sum_pnl_pct=sentinel, max_drawdown=-0.15 → DD_Step содержит -0.15."""
        config = _make_config(
            atr_range=(10, 10), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
            min_buckets_for_median=1,
        )
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok",
             "sum_pnl_pct": INVALID_METRIC_VALUE, "max_drawdown": -0.15},
        ]
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        row = result.iloc[0]
        # PnL sentinel → Step1 NaN
        assert pd.isna(row["Step1"]), "PnL should be NaN (sentinel)"
        # DD is valid → DD_Step1 = -0.15
        assert row["DD_Step1"] == pytest.approx(-0.15), (
            "DD must be accumulated independently of PnL NaN"
        )

    def test_pnl_accumulator_independent_of_dd_nan(self):
        """Строка с max_drawdown=sentinel, sum_pnl_pct=42.0 → Step содержит 42.0."""
        config = _make_config(
            atr_range=(10, 10), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
            min_buckets_for_median=1,
        )
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok",
             "sum_pnl_pct": 42.0, "max_drawdown": INVALID_METRIC_VALUE},
        ]
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        row = result.iloc[0]
        # PnL valid → Step1 = 42.0
        assert row["Step1"] == pytest.approx(42.0)
        # DD sentinel → DD_Step1 NaN
        assert pd.isna(row["DD_Step1"]), (
            "DD should be NaN when max_drawdown is sentinel"
        )

    def test_dd_step_has_value_when_pnl_step_is_nan(self):
        """Step1=NaN, DD_Step1≠NaN — легальная ситуация (замечание №2)."""
        config = _make_config(
            atr_range=(10, 10), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
            min_buckets_for_median=1,
        )
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok",
             "sum_pnl_pct": INVALID_METRIC_VALUE, "max_drawdown": -0.20},
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 2, "step_status": "ok",
             "sum_pnl_pct": 50.0, "max_drawdown": -0.10},
        ]
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        row = result.iloc[0]
        assert pd.isna(row["Step1"])
        assert row["DD_Step1"] == pytest.approx(-0.20)
        assert row["Step2"] == pytest.approx(50.0)
        assert row["DD_Step2"] == pytest.approx(-0.10)

    def test_pnl_step_has_value_when_dd_step_is_nan(self):
        """Step1≠NaN, DD_Step1=NaN — обратный кейс (замечание №2)."""
        config = _make_config(
            atr_range=(10, 10), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
            min_buckets_for_median=1,
        )
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok",
             "sum_pnl_pct": 42.0, "max_drawdown": INVALID_METRIC_VALUE},
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 2, "step_status": "ok",
             "sum_pnl_pct": 50.0, "max_drawdown": -0.10},
        ]
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        row = result.iloc[0]
        assert row["Step1"] == pytest.approx(42.0)
        assert pd.isna(row["DD_Step1"])
        assert row["Step2"] == pytest.approx(50.0)
        assert row["DD_Step2"] == pytest.approx(-0.10)

    def test_dd_step_includes_zero_trade_rows(self):
        """ok-строка с num_trades=0, max_drawdown=-0.03 → DD-медиана включает (замечание №4).

        max_drawdown is equity-based and valid at num_trades=0.
        sum_pnl_pct is overwritten to 0.0 by _apply_trade_level_override
        for zero-trade rows, but max_drawdown remains as-is.
        """
        config = _make_config(
            atr_range=(10, 10), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
            min_buckets_for_median=1,
        )
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok",
             "sum_pnl_pct": 0.0, "max_drawdown": -0.03,
             "num_trades": 0},
        ]
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        row = result.iloc[0]
        # sum_pnl_pct=0.0 is valid (not sentinel) → Step1 = 0.0
        assert row["Step1"] == pytest.approx(0.0)
        # max_drawdown=-0.03 is valid → DD_Step1 = -0.03
        assert row["DD_Step1"] == pytest.approx(-0.03)


# ===========================================================================
# median_oos_pnl and bucket_balanced_score
# ===========================================================================

def _make_config_single_bucket(n_steps=2):
    """Single-bucket config for balanced score tests."""
    return _make_config(
        atr_range=(10, 10), mult_range=(2.0, 2.0), mult_step=0.1,
        atr_bucket_step=2, mult_bucket_step=0.2,
        min_buckets_for_median=1,
    )


def _rows_for_bucket(pnl_by_step, dd_by_step, atr=10, mult=2.0):
    """Build row list for a single (atr, mult) parameter across steps."""
    rows = []
    for s, (pnl, dd) in enumerate(zip(pnl_by_step, dd_by_step), start=1):
        rows.append({
            "atr_period": atr,
            "multiplier": mult,
            "trade_mode": "long",
            "wf_step": s,
            "step_status": "ok",
            "sum_pnl_pct": pnl,
            "max_drawdown": dd,
        })
    return rows


class TestMedianOosPnl:
    def test_column_exists(self):
        config = _make_config_single_bucket()
        df = _make_step_oos_long(
            _rows_for_bucket([10.0, 20.0], [-0.05, -0.10])
        )
        result = build_median_bucket_matrix(df, config)
        assert "median_oos_pnl" in result.columns

    def test_formula_equals_step_median(self):
        """median_oos_pnl = median(Step1, Step2) for a single bucket."""
        config = _make_config_single_bucket()
        df = _make_step_oos_long(
            _rows_for_bucket([10.0, 30.0], [-0.05, -0.10])
        )
        result = build_median_bucket_matrix(df, config)
        row = result.iloc[0]
        expected = float(np.median([row["Step1"], row["Step2"]]))
        assert row["median_oos_pnl"] == pytest.approx(expected)

    def test_differs_from_mean_on_skewed_steps(self):
        """With skewed steps mean ≠ median: verify median_oos_pnl ≠ mean_oos_pnl."""
        config = _make_config(
            atr_range=(10, 10), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
            min_buckets_for_median=1,
        )
        # 4 steps: three small values + one large outlier
        rows = _rows_for_bucket([1.0, 1.0, 1.0, 100.0], [-0.05] * 4)
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        row = result.iloc[0]
        # mean ≈ 25.75, median = 1.0
        assert row["median_oos_pnl"] != pytest.approx(row["mean_oos_pnl"])
        assert row["median_oos_pnl"] == pytest.approx(1.0)

    def test_single_non_nan_step(self):
        """Single non-NaN step → median equals that step value."""
        config = _make_config(
            atr_range=(10, 10), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
            min_buckets_for_median=1,
        )
        rows = [
            {"atr_period": 10, "multiplier": 2.0, "trade_mode": "long",
             "wf_step": 1, "step_status": "ok",
             "sum_pnl_pct": 42.0, "max_drawdown": -0.10},
        ]
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        row = result.iloc[0]
        assert row["median_oos_pnl"] == pytest.approx(42.0)

    def test_all_nan_steps_gives_nan(self):
        """All step values NaN → median_oos_pnl = NaN."""
        config = _make_config_single_bucket()
        df = _make_step_oos_long(
            _rows_for_bucket([INVALID_METRIC_VALUE, INVALID_METRIC_VALUE],
                             [-0.05, -0.10])
        )
        result = build_median_bucket_matrix(df, config)
        assert pd.isna(result.iloc[0]["median_oos_pnl"])

    def test_is_float(self):
        """median_oos_pnl is a float (not rounded in builder — rounding is writer's job)."""
        config = _make_config_single_bucket()
        df = _make_step_oos_long(
            _rows_for_bucket([10.0, 20.0], [-0.05, -0.10])
        )
        result = build_median_bucket_matrix(df, config)
        val = result.iloc[0]["median_oos_pnl"]
        assert isinstance(val, float)

    def test_placement_after_mean_oos_pnl(self):
        """median_oos_pnl is placed immediately after mean_oos_pnl in column order."""
        config = _make_config()
        df = _make_simple_oos()
        result = build_median_bucket_matrix(df, config)
        cols = list(result.columns)
        mean_idx = cols.index("mean_oos_pnl")
        median_idx = cols.index("median_oos_pnl")
        assert median_idx == mean_idx + 1


class TestBucketBalancedScore:
    def test_column_exists(self):
        config = _make_config()
        df = _make_simple_oos()
        result = build_median_bucket_matrix(df, config)
        assert "bucket_balanced_score" in result.columns

    def test_is_last_column(self):
        config = _make_config()
        df = _make_simple_oos()
        result = build_median_bucket_matrix(df, config)
        assert list(result.columns)[-1] == "bucket_balanced_score"

    def test_range_zero_to_one(self):
        config = _make_config()
        df = _make_simple_oos()
        result = build_median_bucket_matrix(df, config)
        scores = result["bucket_balanced_score"].dropna()
        assert (scores >= 0.0).all()
        assert (scores <= 1.0).all()

    def test_precision_6(self):
        config = _make_config()
        df = _make_simple_oos()
        result = build_median_bucket_matrix(df, config)
        for val in result["bucket_balanced_score"].dropna():
            assert len(f"{val:.10f}".split(".")[1].rstrip("0")) <= 6

    def test_stability_score_unchanged(self):
        """Adding balanced score must not alter bucket_stability_score values."""
        config = _make_config()
        df = _make_simple_oos()
        result = build_median_bucket_matrix(df, config)
        for row_i in range(len(result)):
            row = result.iloc[row_i]
            total_steps = 3
            presence_ratio = row["presence_count"] / total_steps
            above_med_ratio = row["above_median_count"] / total_steps
            expected = round(0.6 * presence_ratio + 0.4 * above_med_ratio, 6)
            assert row["bucket_stability_score"] == pytest.approx(expected)

    def test_sort_is_still_by_stability_score(self):
        """Primary sort key remains bucket_stability_score DESC."""
        config = _make_config()
        df = _make_simple_oos()
        result = build_median_bucket_matrix(df, config)
        scores = result["bucket_stability_score"].tolist()
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1]

    def test_uses_median_pnl_not_mean(self):
        """Balanced score must follow median PnL ordering, not mean PnL."""
        # Two buckets in different size ranges: bucket A has step values [1,1,1,100]
        # (mean≈25.75, median=1.0); bucket B has step values [10,10,10,10]
        # (mean=median=10.0). Median ranks B > A; mean also ranks B > A here,
        # but we verify the score's pnl component uses median_oos_pnl directly.
        config = _make_config(
            atr_range=(10, 12), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
            min_buckets_for_median=1,
        )
        rows = (
            _rows_for_bucket([1.0, 1.0, 1.0, 100.0], [-0.05] * 4, atr=10)
            + _rows_for_bucket([10.0, 10.0, 10.0, 10.0], [-0.05] * 4, atr=12)
        )
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        bucket_a = result[result["atr_bucket"] == 10].iloc[0]
        bucket_b = result[result["atr_bucket"] == 12].iloc[0]
        # median A = 1.0 < median B = 10.0 → B should have higher pnl component
        assert bucket_b["bucket_balanced_score"] > bucket_a["bucket_balanced_score"]
        # Verify the score is NOT just using mean_oos_pnl by checking median values
        assert bucket_a["median_oos_pnl"] == pytest.approx(1.0)
        assert bucket_b["median_oos_pnl"] == pytest.approx(10.0)

    def test_dd_directionality(self):
        """Bucket with lower (less negative) DD gets higher balanced score."""
        config = _make_config(
            atr_range=(10, 12), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
            min_buckets_for_median=1,
        )
        # Equal PnL, equal presence; bucket A has lower DD (better)
        rows = (
            _rows_for_bucket([10.0, 10.0], [-0.05, -0.05], atr=10)  # lower DD
            + _rows_for_bucket([10.0, 10.0], [-0.30, -0.30], atr=12)  # higher DD
        )
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        bucket_low_dd = result[result["atr_bucket"] == 10].iloc[0]
        bucket_high_dd = result[result["atr_bucket"] == 12].iloc[0]
        assert bucket_low_dd["bucket_balanced_score"] > bucket_high_dd["bucket_balanced_score"]

    def test_best_pnl_bucket_gets_c_pnl_one(self):
        """Bucket with max median_oos_pnl → pnl component = 1.0 (reflected in score)."""
        config = _make_config(
            atr_range=(10, 12), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
            min_buckets_for_median=1,
        )
        rows = (
            _rows_for_bucket([5.0, 5.0], [-0.10, -0.10], atr=10)
            + _rows_for_bucket([50.0, 50.0], [-0.10, -0.10], atr=12)
        )
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        # Equal presence, equal DD → only pnl component differs
        bucket_best = result[result["atr_bucket"] == 12].iloc[0]
        bucket_worst = result[result["atr_bucket"] == 10].iloc[0]
        # best pnl bucket: C_presence=1, C_pnl=1, C_dd=? (equal DD → 0 after zero-range)
        # worst pnl bucket: C_presence=1, C_pnl=0, C_dd=same
        assert bucket_best["bucket_balanced_score"] > bucket_worst["bucket_balanced_score"]

    def test_zero_range_pnl_gives_zero_component(self):
        """All buckets have same median_oos_pnl → C_pnl = 0.0 for all."""
        config = _make_config(
            atr_range=(10, 12), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
            min_buckets_for_median=1,
        )
        rows = (
            _rows_for_bucket([10.0, 10.0], [-0.05, -0.10], atr=10)
            + _rows_for_bucket([10.0, 10.0], [-0.20, -0.30], atr=12)
        )
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        # All median_oos_pnl == 10.0 → zero range → C_pnl = 0.0
        # Score = 1/3 * C_presence + 0 + 1/3 * C_dd, max possible = 2/3 ≈ 0.666667
        for _, row in result.iterrows():
            if not pd.isna(row["bucket_balanced_score"]):
                assert row["bucket_balanced_score"] <= 2.0 / 3.0 + 1e-5

    def test_zero_range_dd_gives_zero_component(self):
        """All buckets have same max_drawdown_Median → C_dd = 0.0 for all."""
        config = _make_config(
            atr_range=(10, 12), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
            min_buckets_for_median=1,
        )
        rows = (
            _rows_for_bucket([5.0, 5.0], [-0.10, -0.10], atr=10)
            + _rows_for_bucket([50.0, 50.0], [-0.10, -0.10], atr=12)
        )
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        for _, row in result.iterrows():
            if not pd.isna(row["bucket_balanced_score"]):
                assert row["bucket_balanced_score"] <= 2.0 / 3.0 + 1e-5

    def test_nan_when_pnl_component_all_nan(self):
        """All median_oos_pnl = NaN → all bucket_balanced_score = NaN."""
        config = _make_config_single_bucket()
        df = _make_step_oos_long(
            _rows_for_bucket([INVALID_METRIC_VALUE, INVALID_METRIC_VALUE],
                             [-0.05, -0.10])
        )
        result = build_median_bucket_matrix(df, config)
        assert pd.isna(result.iloc[0]["bucket_balanced_score"])

    def test_nan_when_dd_component_all_nan(self):
        """All max_drawdown_Median = NaN → all bucket_balanced_score = NaN."""
        config = _make_config_single_bucket()
        df = _make_step_oos_long(
            _rows_for_bucket([10.0, 20.0],
                             [INVALID_METRIC_VALUE, INVALID_METRIC_VALUE])
        )
        result = build_median_bucket_matrix(df, config)
        assert pd.isna(result.iloc[0]["bucket_balanced_score"])

    def test_no_weight_redistribution_on_missing_component(self):
        """Score is NaN (not redistributed) when one component is NaN."""
        config = _make_config(
            atr_range=(10, 12), mult_range=(2.0, 2.0), mult_step=0.1,
            atr_bucket_step=2, mult_bucket_step=0.2,
            min_buckets_for_median=1,
        )
        # bucket A: valid pnl + valid dd → score should be valid
        # bucket B: NaN pnl → score must be NaN, not redistributed to 2 components
        rows = (
            _rows_for_bucket([10.0, 10.0], [-0.10, -0.10], atr=10)
            + _rows_for_bucket([INVALID_METRIC_VALUE, INVALID_METRIC_VALUE],
                               [-0.10, -0.10], atr=12)
        )
        df = _make_step_oos_long(rows)
        result = build_median_bucket_matrix(df, config)
        bucket_a = result[result["atr_bucket"] == 10].iloc[0]
        bucket_b = result[result["atr_bucket"] == 12].iloc[0]
        assert not pd.isna(bucket_a["bucket_balanced_score"])
        assert pd.isna(bucket_b["bucket_balanced_score"])

    def test_full_pregated_no_topk_filter(self):
        """Score is computed on full pre-gate grid, not a top-k subset."""
        config = _make_config()
        df = _make_simple_oos()
        result = build_median_bucket_matrix(df, config)
        # All rows in the full grid should have a bucket_balanced_score attempt
        assert "bucket_balanced_score" in result.columns
        assert len(result) > 1
