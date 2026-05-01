"""
Tests for A11: Summary builder (long→wide, Block A/B/C)

Coverage:
  - One row per grid_point_id (invariant §12.3)
  - grid_rank is first column (invariant §12.4)
  - All rows preserved (invariant §12.5)
  - Segment columns in order S1..SN (invariant §12.6)
  - Block A columns before Block B before Block C (invariant §12.9)
  - Segment metric values correctly pivoted
  - n_segments reflects number of WF steps
  - Identity columns (atr_period, multiplier, trade_mode) parsed from gp_id
  - ranking_mode propagated from config
  - ok_ratio propagated from aggregated
  - Empty inputs return empty DataFrame
"""

from __future__ import annotations

import pandas as pd
import pytest

from wf_grid.config.schema import DataConfig, GridConfig, RankingConfig
from wf_grid.export.summary_builder import (
    _BLOCK_A,
    _BLOCK_B,
    _is_segment_col,
    _sorted_segment_columns,
    build_summary_wide,
)
from wf_grid.status.status_model import StepStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(mode: str = "gates_score") -> GridConfig:
    return GridConfig(
        data=DataConfig(file_path="dummy.csv"),
        ranking=RankingConfig(mode=mode),
    )


def _make_step_row(
    gp_id: str,
    wf_step: int,
    status: str = StepStatus.OK.value,
    sum_pnl_pct: float = 5.0,
    num_trades: int = 10,
    max_drawdown: float = -0.10,
    sharpe: float = 1.2,
    sortino: float = 1.5,
    cagr: float = 0.15,
    win_rate: float = 0.6,
    profit_factor: float = 1.8,
    avg_trade: float = 0.5,
) -> dict:
    return {
        "grid_point_id": gp_id,
        "wf_step": wf_step,
        "step_status": status,
        "sum_pnl_pct": sum_pnl_pct,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_drawdown,
        "cagr": cagr,
        "win_rate": win_rate,
        "num_trades": num_trades,
        "profit_factor": profit_factor,
        "avg_trade": avg_trade,
        "prepend_bars_applied": 50,
        "effective_oos_bars": 100,
    }


def _make_agg_row(
    gp_id: str,
    n_ok: int = 2,
    n_total: int = 2,
    pnl_median: float = 5.0,
    pnl_min: float = 1.0,
    pnl_std: float = 2.0,
    ok_ratio: float = 1.0,
) -> dict:
    return {
        "grid_point_id": gp_id,
        "n_ok_steps": n_ok,
        "n_total_steps": n_total,
        "ok_ratio": ok_ratio,
        "sum_pnl_pct_Mean": pnl_median,
        "sum_pnl_pct_Std": pnl_std,
        "sum_pnl_pct_Min": pnl_min,
        "sum_pnl_pct_Max": 10.0,
        "sum_pnl_pct_Median": pnl_median,
        "num_trades_Median": 10.0,
        "max_drawdown_Min": -0.20,
        "profit_factor_Median": 1.8,
        "sharpe_Median": 1.2,
        "sortino_Median": 1.5,
        "cagr_Median": 0.15,
        "win_rate_Median": 0.6,
        "avg_trade_Median": 0.5,
        "profitable_segments_count": n_ok,
        "total_oos_trades": n_ok * 10,
        "has_defensive_fallback_steps": False,
    }


def _make_ranked_row(
    gp_id: str,
    grid_rank: int = 1,
    tier: int = 1,
    seed_gate_passed: bool = True,
    seed_score: float = 0.8,
    pnl_median: float = 5.0,
    pnl_min: float = 1.0,
    pnl_std: float = 2.0,
    n_ok: int = 2,
    n_total: int = 2,
    ok_ratio: float = 1.0,
) -> dict:
    return {
        "grid_rank": grid_rank,
        "grid_point_id": gp_id,
        "tier": tier,
        "n_ok_steps": n_ok,
        "n_total_steps": n_total,
        "ok_ratio": ok_ratio,
        "seed_gate_passed": seed_gate_passed,
        "tester_seed_score": seed_score,
        "score_contract_status": "ok" if seed_gate_passed else "no_score",
        "score_discrimination_status": "ok" if seed_gate_passed else "no_score",
        "gate_ok_positive_median": seed_gate_passed,
        "gate_ok_min_trades": seed_gate_passed,
        "gate_ok_worst_segment": seed_gate_passed,
        "gate_ok_drawdown": seed_gate_passed,
        "seed_gate_fail_reason": "",
        "sum_pnl_pct_Median": pnl_median,
        "sum_pnl_pct_Min": pnl_min,
        "sum_pnl_pct_Std": pnl_std,
        "max_drawdown_Min": -0.20,
        "num_trades_Median": 10.0,
        "profit_factor_Median": 1.8,
        "sharpe_Median": 1.2,
        "sortino_Median": 1.5,
        "cagr_Median": 0.15,
        "win_rate_Median": 0.6,
        "avg_trade_Median": 0.5,
        "profitable_segments_count": n_ok,
    }


def _build(gp_ids, n_steps=2, cfg=None):
    """Build standard test inputs for given gp_ids with n_steps each."""
    cfg = cfg or _cfg()
    steps = []
    for gp_id in gp_ids:
        for s in range(1, n_steps + 1):
            steps.append(_make_step_row(gp_id, s))

    agg_rows = [_make_agg_row(g) for g in gp_ids]
    ranked_rows = [
        _make_ranked_row(g, grid_rank=i + 1) for i, g in enumerate(gp_ids)
    ]
    return (
        pd.DataFrame(steps),
        pd.DataFrame(agg_rows),
        pd.DataFrame(ranked_rows),
        cfg,
    )


# ===========================================================================
# Basic invariants
# ===========================================================================

class TestInvariants:
    def test_one_row_per_grid_point(self):
        step_df, agg_df, ranked_df, cfg = _build(["gp1", "gp2"])
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert len(result) == 2
        assert result["grid_point_id"].nunique() == 2

    def test_grid_rank_is_first_column(self):
        step_df, agg_df, ranked_df, cfg = _build(["gp1"])
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert result.columns[0] == "grid_rank"

    def test_all_rows_preserved(self):
        step_df, agg_df, ranked_df, cfg = _build(["gp1", "gp2", "gp3"])
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert len(result) == 3

    def test_empty_inputs_return_empty(self):
        result = build_summary_wide(
            pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), _cfg()
        )
        assert result.empty


# ===========================================================================
# Segment columns (xlsx spec §5, §12.6)
# ===========================================================================

class TestSegmentColumns:
    def test_segment_columns_present(self):
        step_df, agg_df, ranked_df, cfg = _build(["gp1"], n_steps=2)
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert "S1_sum_pnl_pct" in result.columns
        assert "S2_sum_pnl_pct" in result.columns

    def test_segment_columns_in_order_s1_before_s2(self):
        step_df, agg_df, ranked_df, cfg = _build(["gp1"], n_steps=3)
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        seg_cols = [c for c in result.columns if _is_segment_col(c)]
        nums = [int(c.split("_")[0][1:]) for c in seg_cols]
        assert nums == sorted(nums)

    def test_segment_metric_value_propagated(self):
        steps = [
            _make_step_row("gp1", 1, sum_pnl_pct=7.77),
            _make_step_row("gp1", 2, sum_pnl_pct=3.33),
        ]
        step_df = pd.DataFrame(steps)
        agg_df = pd.DataFrame([_make_agg_row("gp1")])
        ranked_df = pd.DataFrame([_make_ranked_row("gp1")])
        result = build_summary_wide(step_df, agg_df, ranked_df, _cfg())
        row = result[result["grid_point_id"] == "gp1"].iloc[0]
        assert row["S1_sum_pnl_pct"] == pytest.approx(7.77)
        assert row["S2_sum_pnl_pct"] == pytest.approx(3.33)

    def test_n_segments_correct(self):
        step_df, agg_df, ranked_df, cfg = _build(["gp1"], n_steps=3)
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert result.iloc[0]["n_segments"] == 3


# ===========================================================================
# Block A / B / C ordering (xlsx spec §10, §12.9)
# ===========================================================================

class TestBlockOrdering:
    def test_block_a_cols_before_block_b(self):
        step_df, agg_df, ranked_df, cfg = _build(["gp1"])
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        cols = list(result.columns)

        a_positions = [cols.index(c) for c in _BLOCK_A if c in cols]
        b_positions = [cols.index(c) for c in _BLOCK_B if c in cols]

        if a_positions and b_positions:
            assert max(a_positions) < min(b_positions)

    def test_block_b_cols_before_segment_cols(self):
        step_df, agg_df, ranked_df, cfg = _build(["gp1"], n_steps=2)
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        cols = list(result.columns)

        b_positions = [cols.index(c) for c in _BLOCK_B if c in cols]
        seg_positions = [cols.index(c) for c in cols if _is_segment_col(c)]

        if b_positions and seg_positions:
            assert max(b_positions) < min(seg_positions)

    def test_seed_gate_passed_in_block_a_range(self):
        step_df, agg_df, ranked_df, cfg = _build(["gp1"])
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        cols = list(result.columns)
        assert "seed_gate_passed" in cols
        # It should be before the first Block B column that is present
        b_present = [c for c in _BLOCK_B if c in cols]
        if b_present:
            assert cols.index("seed_gate_passed") < cols.index(b_present[0])


# ===========================================================================
# Identity columns from grid_point_id
# ===========================================================================

class TestIdentityColumns:
    def test_atr_period_parsed(self):
        step_df, agg_df, ranked_df, cfg = _build(["atr10_m2.50_both"])
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert result.iloc[0]["atr_period"] == 10

    def test_multiplier_parsed(self):
        step_df, agg_df, ranked_df, cfg = _build(["atr10_m2.50_both"])
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert result.iloc[0]["multiplier"] == pytest.approx(2.50)

    def test_trade_mode_parsed(self):
        step_df, agg_df, ranked_df, cfg = _build(["atr10_m2.50_both"])
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert result.iloc[0]["trade_mode"] == "both"


# ===========================================================================
# ranking_mode and ok_ratio propagation
# ===========================================================================

class TestMetaPropagation:
    def test_ranking_mode_propagated(self):
        step_df, agg_df, ranked_df, cfg = _build(["gp1"], cfg=_cfg(mode="legacy"))
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert result.iloc[0]["ranking_mode"] == "legacy"

    def test_ok_ratio_present(self):
        step_df, agg_df, ranked_df, cfg = _build(["gp1"])
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert "ok_ratio" in result.columns

    def test_tester_seed_score_present(self):
        step_df, agg_df, ranked_df, cfg = _build(["gp1"])
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert "tester_seed_score" in result.columns


# ===========================================================================
# Helper unit tests
# ===========================================================================

class TestHelpers:
    def test_sorted_segment_columns_order(self):
        cols = ["S3_pnl", "S1_pnl", "S2_pnl", "S10_pnl"]
        result = _sorted_segment_columns(cols)
        assert result == ["S1_pnl", "S2_pnl", "S3_pnl", "S10_pnl"]

    def test_is_segment_col(self):
        assert _is_segment_col("S1_sum_pnl_pct") is True
        assert _is_segment_col("S12_metric") is True
        assert _is_segment_col("sum_pnl_pct_Median") is False
        assert _is_segment_col("grid_rank") is False


# ===========================================================================
# FIX-3.1 — warning columns in summary
# ===========================================================================

class TestWarningColumns:
    """FIX-3.1: total_oos_trades, has_defensive_fallback_steps, reliability_flag, n_passed_for_scoring."""

    def test_total_oos_trades_present(self):
        step_df, agg_df, ranked_df, cfg = _build(["gp1"])
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert "total_oos_trades" in result.columns

    def test_has_defensive_fallback_present(self):
        step_df, agg_df, ranked_df, cfg = _build(["gp1"])
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert "has_defensive_fallback_steps" in result.columns

    def test_reliability_flag_present(self):
        step_df, agg_df, ranked_df, cfg = _build(["gp1"])
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert "reliability_flag" in result.columns

    def test_n_passed_for_scoring_present(self):
        step_df, agg_df, ranked_df, cfg = _build(["gp1"])
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert "n_passed_for_scoring" in result.columns

    def test_reliability_flag_low(self):
        """3 ok steps, 9 trades → LOW."""
        step_df, agg_df, ranked_df, cfg = _build(["gp1"])
        agg_df["total_oos_trades"] = 9
        agg_df["n_ok_steps"] = 3
        agg_df["ok_ratio"] = 1.0
        ranked_df["n_ok_steps"] = 3
        ranked_df["ok_ratio"] = 1.0
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert result.iloc[0]["reliability_flag"] == "LOW"

    def test_reliability_flag_high(self):
        """8 ok steps, 50 trades, ok_ratio=0.8 → HIGH."""
        step_df, agg_df, ranked_df, cfg = _build(["gp1"])
        agg_df["total_oos_trades"] = 50
        agg_df["n_ok_steps"] = 8
        agg_df["ok_ratio"] = 0.8
        ranked_df["n_ok_steps"] = 8
        ranked_df["ok_ratio"] = 0.8
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert result.iloc[0]["reliability_flag"] == "HIGH"

    def test_reliability_flag_medium(self):
        """4 ok steps, 20 trades, ok_ratio=0.5 → MEDIUM."""
        step_df, agg_df, ranked_df, cfg = _build(["gp1"])
        agg_df["total_oos_trades"] = 20
        agg_df["n_ok_steps"] = 4
        agg_df["ok_ratio"] = 0.5
        ranked_df["n_ok_steps"] = 4
        ranked_df["ok_ratio"] = 0.5
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert result.iloc[0]["reliability_flag"] == "MEDIUM"

    def test_n_passed_for_scoring_value(self):
        """Two candidates, one passed → n_passed_for_scoring = 1."""
        step_df, agg_df, ranked_df, cfg = _build(["gp1", "gp2"])
        ranked_df.loc[ranked_df["grid_point_id"] == "gp2", "seed_gate_passed"] = False
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert result.iloc[0]["n_passed_for_scoring"] == 1
        assert result.iloc[1]["n_passed_for_scoring"] == 1

    def test_columns_in_block_a(self):
        """Warning / discrimination columns must be in Block A (before Block B)."""
        for col in ["total_oos_trades", "has_defensive_fallback_steps",
                     "reliability_flag", "n_passed_for_scoring",
                     "score_discrimination_status"]:
            assert col in _BLOCK_A, f"{col} not in _BLOCK_A"


# ===========================================================================
# sum_pnl_pct_Sum in Block B
# ===========================================================================

class TestSumPnlPctSumInSummary:
    """sum_pnl_pct_Sum must appear in Block B, after sum_pnl_pct_Median."""

    def test_sum_pnl_pct_sum_in_block_b(self):
        assert "sum_pnl_pct_Sum" in _BLOCK_B

    def test_sum_pnl_pct_sum_after_median_in_block_b(self):
        median_idx = _BLOCK_B.index("sum_pnl_pct_Median")
        sum_idx = _BLOCK_B.index("sum_pnl_pct_Sum")
        assert sum_idx == median_idx + 1, (
            f"sum_pnl_pct_Sum should be immediately after sum_pnl_pct_Median in _BLOCK_B, "
            f"but median is at {median_idx} and Sum is at {sum_idx}"
        )

    def test_sum_pnl_pct_sum_in_summary_output(self):
        step_df, agg_df, ranked_df, cfg = _build(["gp1"])
        agg_df["sum_pnl_pct_Sum"] = 42.0
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert "sum_pnl_pct_Sum" in result.columns

    def test_sum_pnl_pct_sum_value_propagated(self):
        step_df, agg_df, ranked_df, cfg = _build(["gp1"])
        agg_df["sum_pnl_pct_Sum"] = 77.5
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert result.iloc[0]["sum_pnl_pct_Sum"] == pytest.approx(77.5)

    def test_sum_pnl_pct_sum_in_block_b_position(self):
        """sum_pnl_pct_Sum must appear in Block B range (after Block A, before segment cols)."""
        step_df, agg_df, ranked_df, cfg = _build(["gp1"])
        agg_df["sum_pnl_pct_Sum"] = 10.0
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        cols = list(result.columns)

        if "sum_pnl_pct_Sum" not in cols:
            pytest.skip("sum_pnl_pct_Sum not present (agg not merged)")

        from wf_grid.export.summary_builder import _is_segment_col
        a_positions = [cols.index(c) for c in _BLOCK_A if c in cols]
        seg_positions = [cols.index(c) for c in cols if _is_segment_col(c)]
        sum_pos = cols.index("sum_pnl_pct_Sum")

        if a_positions:
            assert sum_pos > max(a_positions)
        if seg_positions:
            assert sum_pos < min(seg_positions)


# ===========================================================================
# A1: aggregation_scope marker (plan §4.6)
# ===========================================================================

class TestAggregationScopeMarker:
    """A1: summary_wide must contain aggregation_scope='ok_steps_only' for every row."""

    def test_aggregation_scope_column_present(self):
        """aggregation_scope column must exist in summary_wide output."""
        step_df, agg_df, ranked_df, cfg = _build(["gp1"])
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert "aggregation_scope" in result.columns

    def test_aggregation_scope_value_is_ok_steps_only(self):
        """aggregation_scope must equal 'ok_steps_only' for every row."""
        step_df, agg_df, ranked_df, cfg = _build(["gp1", "gp2", "gp3"])
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert (result["aggregation_scope"] == "ok_steps_only").all(), (
            "All rows must have aggregation_scope='ok_steps_only'"
        )

    def test_aggregation_scope_in_block_a(self):
        """aggregation_scope must be declared in _BLOCK_A so it appears early."""
        assert "aggregation_scope" in _BLOCK_A, (
            "aggregation_scope must be in _BLOCK_A per plan A1"
        )

    def test_aggregation_scope_before_block_b_columns(self):
        """aggregation_scope column must appear before any Block B column in the output."""
        step_df, agg_df, ranked_df, cfg = _build(["gp1"])
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        cols = list(result.columns)
        scope_pos = cols.index("aggregation_scope")
        # Any Block B column that's present must be after aggregation_scope
        block_b_present = [c for c in _BLOCK_B if c in cols]
        if block_b_present:
            assert scope_pos < min(cols.index(c) for c in block_b_present), (
                "aggregation_scope (Block A) must precede all Block B columns"
            )


# ===========================================================================
# S1: n_segments sparse fix (plan §4.9)
# ===========================================================================

class TestNSegmentsSparse:
    """n_segments must use nunique() not max()."""

    def test_n_segments_contiguous(self):
        """Standard case: steps 1,2,3 → n_segments == 3."""
        step_df, agg_df, ranked_df, cfg = _build(["gp1"], n_steps=3)
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert result.iloc[0]["n_segments"] == 3

    def test_n_segments_sparse_steps(self):
        """Sparse WF steps [1, 3, 5]: max()=5 but nunique()=3 (correct)."""
        steps = [
            _make_step_row("gp1", 1),
            _make_step_row("gp1", 3),
            _make_step_row("gp1", 5),
        ]
        step_df = pd.DataFrame(steps)
        agg_df = pd.DataFrame([_make_agg_row("gp1", n_ok=3, n_total=3)])
        ranked_df = pd.DataFrame([_make_ranked_row("gp1")])
        result = build_summary_wide(step_df, agg_df, ranked_df, _cfg())
        assert result.iloc[0]["n_segments"] == 3, (
            f"Expected 3 (nunique), got {result.iloc[0]['n_segments']} (max would be 5)"
        )

    def test_n_segments_single_step(self):
        """Single WF step: n_segments == 1."""
        step_df, agg_df, ranked_df, cfg = _build(["gp1"], n_steps=1)
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert result.iloc[0]["n_segments"] == 1

    def test_n_segments_two_gps_same_steps(self):
        """Two grid points, each 4 steps: n_segments == 4 for both."""
        step_df, agg_df, ranked_df, cfg = _build(["gp1", "gp2"], n_steps=4)
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert (result["n_segments"] == 4).all()


# ===========================================================================
# B1: bucket_matrix_status visibility (plan §4.9)
# ===========================================================================

class TestBucketMatrixStatusInSummary:
    """B1: bucket_matrix_status and bucket_matrix_error must appear in summary_wide."""

    def test_bucket_matrix_status_column_present(self):
        step_df, agg_df, ranked_df, cfg = _build(["gp1"])
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert "bucket_matrix_status" in result.columns

    def test_bucket_matrix_error_column_present(self):
        step_df, agg_df, ranked_df, cfg = _build(["gp1"])
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert "bucket_matrix_error" in result.columns

    def test_bucket_matrix_status_default_disabled(self):
        """Default (no kwarg) → status == 'disabled'."""
        step_df, agg_df, ranked_df, cfg = _build(["gp1"])
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert (result["bucket_matrix_status"] == "disabled").all()

    def test_bucket_matrix_status_ok(self):
        """When bucket_matrix_status='ok', all rows show 'ok'."""
        step_df, agg_df, ranked_df, cfg = _build(["gp1", "gp2"])
        result = build_summary_wide(
            step_df, agg_df, ranked_df, cfg, bucket_matrix_status="ok"
        )
        assert (result["bucket_matrix_status"] == "ok").all()

    def test_bucket_matrix_status_failed_with_message(self):
        """When status='failed', error message is propagated."""
        step_df, agg_df, ranked_df, cfg = _build(["gp1"])
        result = build_summary_wide(
            step_df, agg_df, ranked_df, cfg,
            bucket_matrix_status="failed",
            bucket_matrix_error="division by zero",
        )
        assert result.iloc[0]["bucket_matrix_status"] == "failed"
        assert result.iloc[0]["bucket_matrix_error"] == "division by zero"

    def test_bucket_matrix_error_na_when_no_error(self):
        """When status='ok' and no error, bucket_matrix_error must be NA."""
        step_df, agg_df, ranked_df, cfg = _build(["gp1"])
        result = build_summary_wide(
            step_df, agg_df, ranked_df, cfg, bucket_matrix_status="ok"
        )
        assert pd.isna(result.iloc[0]["bucket_matrix_error"])

    def test_bucket_matrix_status_in_block_a(self):
        assert "bucket_matrix_status" in _BLOCK_A

    def test_bucket_matrix_error_in_block_a(self):
        assert "bucket_matrix_error" in _BLOCK_A
