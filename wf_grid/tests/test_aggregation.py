"""
Tests for A7: aggregate_candidates

Coverage:
  - Basic shape and column presence
  - ok-only mask: non-ok rows excluded from aggregates
  - Sentinel INVALID_METRIC_VALUE → NaN (§5.3)
  - Capped-extreme profit_factor / sharpe == MAX_VALID_METRIC → NaN (§5.4)
  - Std with < 2 valid values → NaN (§6.3)
  - All-NaN metric → all aggregates NaN
  - ok_ratio computation (§5.5)
  - profitable_segments_count (§7)
  - Empty input → empty DataFrame with correct columns
  - Multiple grid points produce one row each
  - Determinism: same input → same output
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from wf_grid.aggregate.aggregator import (
    _PHASE_A_METRICS,
    _STAT_SUFFIXES,
    _output_columns,
    aggregate_candidates,
)
from wf_grid.config.schema import INVALID_METRIC_VALUE, MAX_VALID_METRIC
from wf_grid.status.status_model import StepStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(
    grid_point_id: str,
    wf_step: int,
    step_status: str = StepStatus.OK.value,
    sum_pnl_pct: float = 5.0,
    sharpe: float = 1.2,
    sortino: float = 1.5,
    max_drawdown: float = -0.1,
    cagr: float = 0.15,
    win_rate: float = 0.6,
    num_trades: int = 10,
    profit_factor: float = 1.8,
    avg_trade: float = 0.5,
) -> dict:
    return {
        "grid_point_id": grid_point_id,
        "wf_step": wf_step,
        "step_status": step_status,
        "sum_pnl_pct": sum_pnl_pct,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_drawdown,
        "cagr": cagr,
        "win_rate": win_rate,
        "num_trades": num_trades,
        "profit_factor": profit_factor,
        "avg_trade": avg_trade,
    }


def _make_df(*rows) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


def _agg(df: pd.DataFrame) -> pd.DataFrame:
    return aggregate_candidates(df, config=None)


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

class TestSchema:
    def test_output_columns_stable(self):
        cols = _output_columns()
        assert cols[0] == "grid_point_id"
        assert "n_ok_steps" in cols
        assert "n_total_steps" in cols
        assert "ok_ratio" in cols
        assert "profitable_segments_count" in cols

    def test_all_metric_stats_present(self):
        cols = _output_columns()
        for m in _PHASE_A_METRICS:
            for s in _STAT_SUFFIXES:
                assert f"{m}_{s}" in cols, f"Missing {m}_{s}"

    def test_empty_input_returns_correct_schema(self):
        df = _agg(pd.DataFrame())
        assert list(df.columns) == _output_columns()
        assert len(df) == 0

    def test_single_ok_step_columns_match(self):
        df = _make_df(_make_row("gp1", 1))
        result = _agg(df)
        canonical = _output_columns()
        present = list(result.columns)
        expected = [c for c in canonical if c in present]
        assert present == expected


# ---------------------------------------------------------------------------
# ok-only mask (§5.2)
# ---------------------------------------------------------------------------

class TestOkMask:
    def test_non_ok_rows_excluded_from_aggregates(self):
        df = _make_df(
            _make_row("gp1", 1, step_status=StepStatus.OK.value, sum_pnl_pct=10.0),
            _make_row("gp1", 2, step_status=StepStatus.NO_TRADES.value, sum_pnl_pct=100.0),
            _make_row("gp1", 3, step_status=StepStatus.RUNTIME_ERROR.value, sum_pnl_pct=999.0),
        )
        result = _agg(df)
        assert result["sum_pnl_pct_Mean"].iloc[0] == pytest.approx(10.0)
        assert result["n_ok_steps"].iloc[0] == 1
        assert result["n_total_steps"].iloc[0] == 3

    def test_all_non_ok_returns_nan_aggregates(self):
        df = _make_df(
            _make_row("gp1", 1, step_status=StepStatus.NO_TRADES.value),
            _make_row("gp1", 2, step_status=StepStatus.INSUFFICIENT_BARS.value),
        )
        result = _agg(df)
        assert result["n_ok_steps"].iloc[0] == 0
        assert result["n_total_steps"].iloc[0] == 2
        assert np.isnan(result["sum_pnl_pct_Mean"].iloc[0])

    def test_only_ok_rows_count_for_profitable_segments(self):
        df = _make_df(
            _make_row("gp1", 1, step_status=StepStatus.OK.value, sum_pnl_pct=5.0),
            _make_row("gp1", 2, step_status=StepStatus.GATE_FAILED.value, sum_pnl_pct=20.0),
        )
        result = _agg(df)
        assert result["profitable_segments_count"].iloc[0] == 1


# ---------------------------------------------------------------------------
# Sentinel handling (§5.3)
# ---------------------------------------------------------------------------

class TestSentinelHandling:
    def test_sentinel_excluded_from_mean(self):
        df = _make_df(
            _make_row("gp1", 1, sum_pnl_pct=10.0),
            _make_row("gp1", 2, sum_pnl_pct=INVALID_METRIC_VALUE),
        )
        result = _agg(df)
        assert result["sum_pnl_pct_Mean"].iloc[0] == pytest.approx(10.0)

    def test_all_sentinel_gives_nan(self):
        df = _make_df(
            _make_row("gp1", 1, sum_pnl_pct=INVALID_METRIC_VALUE),
            _make_row("gp1", 2, sum_pnl_pct=INVALID_METRIC_VALUE),
        )
        result = _agg(df)
        assert np.isnan(result["sum_pnl_pct_Mean"].iloc[0])
        assert np.isnan(result["sum_pnl_pct_Std"].iloc[0])
        assert np.isnan(result["sum_pnl_pct_Min"].iloc[0])
        assert np.isnan(result["sum_pnl_pct_Max"].iloc[0])
        assert np.isnan(result["sum_pnl_pct_Median"].iloc[0])

    def test_sentinel_not_counted_as_profitable(self):
        df = _make_df(
            _make_row("gp1", 1, sum_pnl_pct=INVALID_METRIC_VALUE),
        )
        result = _agg(df)
        assert result["profitable_segments_count"].iloc[0] == 0


# ---------------------------------------------------------------------------
# Capped-extreme handling (§5.4)
# ---------------------------------------------------------------------------

class TestCappedHandling:
    def test_profit_factor_max_valid_excluded(self):
        df = _make_df(
            _make_row("gp1", 1, profit_factor=2.0),
            _make_row("gp1", 2, profit_factor=MAX_VALID_METRIC),
        )
        result = _agg(df)
        assert result["profit_factor_Mean"].iloc[0] == pytest.approx(2.0)

    def test_sharpe_max_valid_excluded(self):
        df = _make_df(
            _make_row("gp1", 1, sharpe=1.5),
            _make_row("gp1", 2, sharpe=MAX_VALID_METRIC),
        )
        result = _agg(df)
        assert result["sharpe_Mean"].iloc[0] == pytest.approx(1.5)

    def test_all_profit_factor_capped_gives_nan(self):
        df = _make_df(
            _make_row("gp1", 1, profit_factor=MAX_VALID_METRIC),
            _make_row("gp1", 2, profit_factor=MAX_VALID_METRIC),
        )
        result = _agg(df)
        assert np.isnan(result["profit_factor_Mean"].iloc[0])

    # FIX-2.4 — sortino and cagr added to _CAPPED_METRICS
    def test_sortino_max_valid_excluded(self):
        """FIX-2.4: sortino == MAX_VALID_METRIC → treated as sentinel → excluded from Mean."""
        df = _make_df(
            _make_row("gp1", 1, sortino=1.8),
            _make_row("gp1", 2, sortino=MAX_VALID_METRIC),
        )
        result = _agg(df)
        assert result["sortino_Mean"].iloc[0] == pytest.approx(1.8)

    def test_cagr_max_valid_excluded(self):
        """FIX-2.4: cagr == MAX_VALID_METRIC → treated as sentinel → excluded from Mean."""
        df = _make_df(
            _make_row("gp1", 1, cagr=0.25),
            _make_row("gp1", 2, cagr=MAX_VALID_METRIC),
        )
        result = _agg(df)
        assert result["cagr_Mean"].iloc[0] == pytest.approx(0.25)

    def test_all_sortino_capped_gives_nan(self):
        """FIX-2.4: all sortino rows capped → all aggregates NaN."""
        df = _make_df(
            _make_row("gp1", 1, sortino=MAX_VALID_METRIC),
            _make_row("gp1", 2, sortino=MAX_VALID_METRIC),
        )
        result = _agg(df)
        assert np.isnan(result["sortino_Mean"].iloc[0])
        assert np.isnan(result["sortino_Median"].iloc[0])

    def test_all_cagr_capped_gives_nan(self):
        """FIX-2.4: all cagr rows capped → all aggregates NaN."""
        df = _make_df(
            _make_row("gp1", 1, cagr=MAX_VALID_METRIC),
            _make_row("gp1", 2, cagr=MAX_VALID_METRIC),
        )
        result = _agg(df)
        assert np.isnan(result["cagr_Mean"].iloc[0])
        assert np.isnan(result["cagr_Median"].iloc[0])

    def test_sharpe_still_capped_regression(self):
        """Regression: sharpe capping still works after FIX-2.4."""
        df = _make_df(
            _make_row("gp1", 1, sharpe=1.5),
            _make_row("gp1", 2, sharpe=MAX_VALID_METRIC),
        )
        result = _agg(df)
        assert result["sharpe_Mean"].iloc[0] == pytest.approx(1.5)

    def test_profit_factor_still_capped_regression(self):
        """Regression: profit_factor capping still works after FIX-2.4."""
        df = _make_df(
            _make_row("gp1", 1, profit_factor=2.0),
            _make_row("gp1", 2, profit_factor=MAX_VALID_METRIC),
        )
        result = _agg(df)
        assert result["profit_factor_Mean"].iloc[0] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# Std with < 2 values (§6.3)
# ---------------------------------------------------------------------------

class TestStdEdgeCases:
    def test_std_nan_with_single_value(self):
        df = _make_df(_make_row("gp1", 1, sum_pnl_pct=10.0))
        result = _agg(df)
        assert np.isnan(result["sum_pnl_pct_Std"].iloc[0])

    def test_std_computed_with_two_values(self):
        df = _make_df(
            _make_row("gp1", 1, sum_pnl_pct=10.0),
            _make_row("gp1", 2, sum_pnl_pct=20.0),
        )
        result = _agg(df)
        expected_std = np.std([10.0, 20.0], ddof=1)
        assert result["sum_pnl_pct_Std"].iloc[0] == pytest.approx(expected_std)

    def test_std_nan_when_only_one_valid_after_sentinel(self):
        # Two rows, one sentinel → one valid → Std must be NaN
        df = _make_df(
            _make_row("gp1", 1, sum_pnl_pct=10.0),
            _make_row("gp1", 2, sum_pnl_pct=INVALID_METRIC_VALUE),
        )
        result = _agg(df)
        assert np.isnan(result["sum_pnl_pct_Std"].iloc[0])


# ---------------------------------------------------------------------------
# ok_ratio (§5.5)
# ---------------------------------------------------------------------------

class TestOkRatio:
    def test_all_ok_ratio_is_one(self):
        df = _make_df(
            _make_row("gp1", 1),
            _make_row("gp1", 2),
        )
        result = _agg(df)
        assert result["ok_ratio"].iloc[0] == pytest.approx(1.0)

    def test_half_ok_ratio(self):
        df = _make_df(
            _make_row("gp1", 1, step_status=StepStatus.OK.value),
            _make_row("gp1", 2, step_status=StepStatus.NO_TRADES.value),
        )
        result = _agg(df)
        assert result["ok_ratio"].iloc[0] == pytest.approx(0.5)
        assert result["n_ok_steps"].iloc[0] == 1
        assert result["n_total_steps"].iloc[0] == 2

    def test_zero_ok_ratio(self):
        df = _make_df(
            _make_row("gp1", 1, step_status=StepStatus.INSUFFICIENT_BARS.value),
        )
        result = _agg(df)
        assert result["ok_ratio"].iloc[0] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# profitable_segments_count (§7)
# ---------------------------------------------------------------------------

class TestProfitableSegmentsCount:
    def test_positive_pnl_counts(self):
        df = _make_df(
            _make_row("gp1", 1, sum_pnl_pct=5.0),
            _make_row("gp1", 2, sum_pnl_pct=-1.0),
            _make_row("gp1", 3, sum_pnl_pct=3.0),
        )
        result = _agg(df)
        assert result["profitable_segments_count"].iloc[0] == 2

    def test_zero_pnl_not_counted(self):
        df = _make_df(
            _make_row("gp1", 1, sum_pnl_pct=0.0),
            _make_row("gp1", 2, sum_pnl_pct=1.0),
        )
        result = _agg(df)
        assert result["profitable_segments_count"].iloc[0] == 1

    def test_all_negative_is_zero(self):
        df = _make_df(
            _make_row("gp1", 1, sum_pnl_pct=-5.0),
            _make_row("gp1", 2, sum_pnl_pct=-2.0),
        )
        result = _agg(df)
        assert result["profitable_segments_count"].iloc[0] == 0


# ---------------------------------------------------------------------------
# Multiple grid points
# ---------------------------------------------------------------------------

class TestMultipleGridPoints:
    def test_one_row_per_grid_point(self):
        df = _make_df(
            _make_row("gp1", 1),
            _make_row("gp1", 2),
            _make_row("gp2", 1),
            _make_row("gp2", 2),
            _make_row("gp3", 1),
        )
        result = _agg(df)
        assert len(result) == 3
        assert set(result["grid_point_id"]) == {"gp1", "gp2", "gp3"}

    def test_aggregates_independent_per_grid_point(self):
        df = _make_df(
            _make_row("gp1", 1, sum_pnl_pct=10.0),
            _make_row("gp1", 2, sum_pnl_pct=20.0),
            _make_row("gp2", 1, sum_pnl_pct=5.0),
            _make_row("gp2", 2, sum_pnl_pct=5.0),
        )
        result = _agg(df)
        gp1 = result[result["grid_point_id"] == "gp1"].iloc[0]
        gp2 = result[result["grid_point_id"] == "gp2"].iloc[0]
        assert gp1["sum_pnl_pct_Mean"] == pytest.approx(15.0)
        assert gp2["sum_pnl_pct_Mean"] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_input_same_output(self):
        df = _make_df(
            _make_row("gp1", 1, sum_pnl_pct=10.0),
            _make_row("gp1", 2, sum_pnl_pct=20.0),
            _make_row("gp2", 1, sum_pnl_pct=5.0),
        )
        r1 = _agg(df)
        r2 = _agg(df)
        pd.testing.assert_frame_equal(r1.reset_index(drop=True), r2.reset_index(drop=True))


# ---------------------------------------------------------------------------
# FIX-2.3 — max_drawdown sign warning in aggregate_candidates
# ---------------------------------------------------------------------------

class TestMaxDrawdownSignWarning:
    """FIX-2.3: Positive max_drawdown in ok rows must trigger a warning."""

    def test_positive_max_drawdown_logs_warning(self, caplog):
        """ok step with max_drawdown=0.05 → warning logged."""
        import logging
        df = _make_df(_make_row("gp1", 1, max_drawdown=0.05))
        with caplog.at_level(logging.WARNING, logger="wf_grid.aggregate.aggregator"):
            _agg(df)
        assert any("max_drawdown > 0" in r.message for r in caplog.records)

    def test_negative_max_drawdown_no_warning(self, caplog):
        """ok step with max_drawdown=-0.10 → no warning."""
        import logging
        df = _make_df(_make_row("gp1", 1, max_drawdown=-0.10))
        with caplog.at_level(logging.WARNING, logger="wf_grid.aggregate.aggregator"):
            _agg(df)
        assert not any("max_drawdown > 0" in r.message for r in caplog.records)

    def test_zero_max_drawdown_no_warning(self, caplog):
        """ok step with max_drawdown=0.0 → no warning (0 is valid boundary)."""
        import logging
        df = _make_df(_make_row("gp1", 1, max_drawdown=0.0))
        with caplog.at_level(logging.WARNING, logger="wf_grid.aggregate.aggregator"):
            _agg(df)
        assert not any("max_drawdown > 0" in r.message for r in caplog.records)

    def test_warning_contains_grid_point_id(self, caplog):
        """Warning message includes the grid_point_id."""
        import logging
        df = _make_df(_make_row("gp_suspect", 1, max_drawdown=0.20))
        with caplog.at_level(logging.WARNING, logger="wf_grid.aggregate.aggregator"):
            _agg(df)
        assert any("gp_suspect" in r.message for r in caplog.records)

    def test_positive_dd_in_non_ok_rows_no_warning(self, caplog):
        """Non-ok rows with positive max_drawdown must not trigger warning."""
        import logging
        df = _make_df(
            _make_row("gp1", 1, step_status=StepStatus.NO_TRADES.value, max_drawdown=0.50),
        )
        with caplog.at_level(logging.WARNING, logger="wf_grid.aggregate.aggregator"):
            _agg(df)
        assert not any("max_drawdown > 0" in r.message for r in caplog.records)

    def test_positive_dd_does_not_block_pipeline(self, caplog):
        """Positive max_drawdown → warning only, aggregation still returns result."""
        import logging
        df = _make_df(_make_row("gp1", 1, max_drawdown=0.05))
        with caplog.at_level(logging.WARNING, logger="wf_grid.aggregate.aggregator"):
            result = _agg(df)
        assert len(result) == 1
        assert result.iloc[0]["grid_point_id"] == "gp1"


# ---------------------------------------------------------------------------
# FIX-3.1 — total_oos_trades and has_defensive_fallback_steps
# ---------------------------------------------------------------------------

class TestTotalOosTrades:
    """FIX-3.1: total_oos_trades = sum of num_trades across ok steps."""

    def test_column_present(self):
        df = _make_df(_make_row("gp1", 1, num_trades=10))
        result = _agg(df)
        assert "total_oos_trades" in result.columns

    def test_single_step(self):
        df = _make_df(_make_row("gp1", 1, num_trades=15))
        result = _agg(df)
        assert result.iloc[0]["total_oos_trades"] == 15

    def test_multiple_steps_summed(self):
        df = _make_df(
            _make_row("gp1", 1, num_trades=10),
            _make_row("gp1", 2, num_trades=20),
            _make_row("gp1", 3, num_trades=5),
        )
        result = _agg(df)
        assert result.iloc[0]["total_oos_trades"] == 35

    def test_non_ok_steps_excluded(self):
        df = _make_df(
            _make_row("gp1", 1, num_trades=10),
            _make_row("gp1", 2, step_status=StepStatus.NO_TRADES.value, num_trades=0),
        )
        result = _agg(df)
        assert result.iloc[0]["total_oos_trades"] == 10

    def test_zero_ok_gives_zero(self):
        df = _make_df(
            _make_row("gp1", 1, step_status=StepStatus.NO_TRADES.value, num_trades=0),
        )
        result = _agg(df)
        assert result.iloc[0]["total_oos_trades"] == 0


class TestHasDefensiveFallbackSteps:
    """FIX-3.1: has_defensive_fallback_steps from used_defensive_fallback column."""

    def test_column_present(self):
        df = _make_df(_make_row("gp1", 1))
        result = _agg(df)
        assert "has_defensive_fallback_steps" in result.columns

    def test_no_fallback_is_false(self):
        df = _make_df(_make_row("gp1", 1))
        if "used_defensive_fallback" not in df.columns:
            df["used_defensive_fallback"] = False
        result = _agg(df)
        assert bool(result.iloc[0]["has_defensive_fallback_steps"]) is False

    def test_with_fallback_is_true(self):
        df = _make_df(_make_row("gp1", 1), _make_row("gp1", 2))
        df["used_defensive_fallback"] = [True, False]
        result = _agg(df)
        assert bool(result.iloc[0]["has_defensive_fallback_steps"]) is True

    def test_all_fallback_is_true(self):
        df = _make_df(_make_row("gp1", 1))
        df["used_defensive_fallback"] = [True]
        result = _agg(df)
        assert bool(result.iloc[0]["has_defensive_fallback_steps"]) is True


# ---------------------------------------------------------------------------
# sum_pnl_pct_Sum
# ---------------------------------------------------------------------------

class TestSumPnlPctSum:
    """sum_pnl_pct_Sum = sum of valid ok-segment PnL values."""

    def test_column_present_in_output(self):
        df = _make_df(_make_row("gp1", 1))
        result = _agg(df)
        assert "sum_pnl_pct_Sum" in result.columns

    def test_column_in_output_columns(self):
        from wf_grid.aggregate.aggregator import _output_columns
        assert "sum_pnl_pct_Sum" in _output_columns()

    def test_basic_sum_two_ok_steps(self):
        df = _make_df(
            _make_row("gp1", 1, sum_pnl_pct=10.0),
            _make_row("gp1", 2, sum_pnl_pct=5.0),
        )
        result = _agg(df)
        assert result["sum_pnl_pct_Sum"].iloc[0] == pytest.approx(15.0)

    def test_only_ok_rows_counted(self):
        df = _make_df(
            _make_row("gp1", 1, step_status=StepStatus.OK.value, sum_pnl_pct=10.0),
            _make_row("gp1", 2, step_status=StepStatus.NO_TRADES.value, sum_pnl_pct=999.0),
        )
        result = _agg(df)
        assert result["sum_pnl_pct_Sum"].iloc[0] == pytest.approx(10.0)

    def test_sentinel_excluded_from_sum(self):
        df = _make_df(
            _make_row("gp1", 1, sum_pnl_pct=10.0),
            _make_row("gp1", 2, sum_pnl_pct=INVALID_METRIC_VALUE),
        )
        result = _agg(df)
        assert result["sum_pnl_pct_Sum"].iloc[0] == pytest.approx(10.0)

    def test_all_sentinel_gives_nan(self):
        df = _make_df(
            _make_row("gp1", 1, sum_pnl_pct=INVALID_METRIC_VALUE),
            _make_row("gp1", 2, sum_pnl_pct=INVALID_METRIC_VALUE),
        )
        result = _agg(df)
        assert np.isnan(result["sum_pnl_pct_Sum"].iloc[0])

    def test_no_ok_rows_gives_nan(self):
        df = _make_df(
            _make_row("gp1", 1, step_status=StepStatus.NO_TRADES.value, sum_pnl_pct=5.0),
            _make_row("gp1", 2, step_status=StepStatus.GATE_FAILED.value, sum_pnl_pct=3.0),
        )
        result = _agg(df)
        assert np.isnan(result["sum_pnl_pct_Sum"].iloc[0])

    def test_negative_values_summed_correctly(self):
        df = _make_df(
            _make_row("gp1", 1, sum_pnl_pct=-4.0),
            _make_row("gp1", 2, sum_pnl_pct=-6.0),
        )
        result = _agg(df)
        assert result["sum_pnl_pct_Sum"].iloc[0] == pytest.approx(-10.0)

    def test_mixed_sign_sum(self):
        df = _make_df(
            _make_row("gp1", 1, sum_pnl_pct=20.0),
            _make_row("gp1", 2, sum_pnl_pct=-5.0),
            _make_row("gp1", 3, sum_pnl_pct=3.0),
        )
        result = _agg(df)
        assert result["sum_pnl_pct_Sum"].iloc[0] == pytest.approx(18.0)

    def test_multiple_grid_points_independent(self):
        df = _make_df(
            _make_row("gp1", 1, sum_pnl_pct=10.0),
            _make_row("gp1", 2, sum_pnl_pct=10.0),
            _make_row("gp2", 1, sum_pnl_pct=3.0),
            _make_row("gp2", 2, sum_pnl_pct=2.0),
        )
        result = _agg(df)
        gp1 = result[result["grid_point_id"] == "gp1"].iloc[0]
        gp2 = result[result["grid_point_id"] == "gp2"].iloc[0]
        assert gp1["sum_pnl_pct_Sum"] == pytest.approx(20.0)
        assert gp2["sum_pnl_pct_Sum"] == pytest.approx(5.0)
