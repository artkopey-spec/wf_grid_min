"""
FIX-2.8 — Golden value regression tests.

Fixed mini-dataset (3 grid points × 3 WF steps) with deterministic inputs.
All numerical pipeline outputs are pinned to exact values via pytest.approx.
Any change to aggregation, gates, scoring, or ranking formulas will break
these tests — golden values must be updated deliberately.

Dataset design:
  gp1: 3 consistent ok steps (all pass gates), profitable_segments=3
  gp2: 3 ok steps but step 2 has negative pnl, profitable_segments=2
  gp3: 3 steps with num_trades < min_trades_required(3) → all gate_failed
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from wf_grid.aggregate.aggregator import aggregate_candidates
from wf_grid.collect.step_collector import collect_oos_steps
from wf_grid.config.schema import (
    BacktestConfig,
    DataConfig,
    GridConfig,
    RankingConfig,
    StatusConfig,
)
from wf_grid.gates.gates import apply_candidate_gates, apply_step_gates
from wf_grid.ranking.ranker import rank_candidates
from wf_grid.ranking.scoring import calculate_seed_score, compute_score_discrimination
from wf_grid.status.status_model import StepStatus
from wf_grid.wf.step_executor import StepResult


# ---------------------------------------------------------------------------
# Fixture: deterministic dataset
# ---------------------------------------------------------------------------

def _step(gp_id, wf, pnl, dd, sharpe, sortino, cagr, n_trades, pf, avg_trade, wr, eff_bars=100):
    return StepResult(
        grid_point_id=gp_id,
        wf_step=wf,
        test_start_idx=0,
        test_end_idx=100,
        metrics={
            "sum_pnl_pct": pnl,
            "sharpe": sharpe,
            "sortino": sortino,
            "max_drawdown": dd,
            "cagr": cagr,
            "win_rate": wr,
            "num_trades": n_trades,
            "profit_factor": pf,
            "avg_trade": avg_trade,
        },
        oos_trades_df=None,
        prepend_bars_requested=50,
        prepend_bars_applied=50,
        used_prepend=True,
        used_legacy_oos_path=False,
        used_defensive_fallback=False,
        oos_boundary_index=50,
        warmup_used=50,
        warmup_effective=50,
        effective_oos_bars=eff_bars,
    )


_GOLDEN_CONFIG = GridConfig(
    data=DataConfig(file_path="dummy.csv"),
    backtest=BacktestConfig(min_trades_required=3),
    status=StatusConfig(min_meaningful_bars=10),
    ranking=RankingConfig(mode="gates_score"),
)

_GOLDEN_RESULTS = {
    "gp1": [
        _step("gp1", 1, 10.0, -0.05, 1.5, 2.0, 0.20, 15, 2.5, 0.67, 0.70),
        _step("gp1", 2, 12.0, -0.08, 1.8, 2.2, 0.25, 20, 3.0, 0.60, 0.75),
        _step("gp1", 3,  8.0, -0.03, 1.2, 1.8, 0.15, 12, 2.0, 0.67, 0.65),
    ],
    "gp2": [
        _step("gp2", 1, 15.0, -0.10, 2.0, 2.5, 0.30, 25, 3.5, 0.60, 0.80),
        _step("gp2", 2, -2.0, -0.15, -0.5, -0.8, -0.05,  5, 0.8, -0.40, 0.40),
        _step("gp2", 3,  7.0, -0.06, 1.0, 1.5, 0.12, 10, 1.8, 0.70, 0.60),
    ],
    "gp3": [
        _step("gp3", 1,  1.0, -0.02, 0.5, 0.6, 0.05, 2, 1.2, 0.50, 0.55),
        _step("gp3", 2,  0.5, -0.01, 0.3, 0.4, 0.02, 1, 1.1, 0.50, 0.50),
        _step("gp3", 3, -0.5, -0.04, -0.2, -0.3, -0.01, 2, 0.9, -0.25, 0.45),
    ],
}


@pytest.fixture(scope="module")
def golden_pipeline():
    """Run pipeline once, cache for all tests in this module."""
    cfg = _GOLDEN_CONFIG
    step_oos = collect_oos_steps(_GOLDEN_RESULTS, cfg, expected_n_steps=3)
    step_oos = apply_step_gates(step_oos, cfg)
    aggregated = aggregate_candidates(step_oos, cfg)
    gated = apply_candidate_gates(aggregated, cfg)
    passed_mask = gated["seed_gate_passed"].fillna(False).astype(bool)
    scores, statuses = calculate_seed_score(gated, passed_mask)
    gated["tester_seed_score"] = scores
    gated["score_contract_status"] = statuses
    gated["score_discrimination_status"] = compute_score_discrimination(
        gated, passed_mask,
    )
    ranked = rank_candidates(gated, cfg)
    return {
        "step_oos": step_oos,
        "aggregated": aggregated,
        "gated": gated,
        "ranked": ranked,
    }


def _get_gp(df, gp_id):
    """Extract single row by grid_point_id."""
    return df[df["grid_point_id"] == gp_id].iloc[0]


# ===========================================================================
# Step-level golden values
# ===========================================================================

class TestGoldenStepStatuses:
    """Verify step_status assignment for the golden dataset."""

    def test_gp1_all_ok(self, golden_pipeline):
        steps = golden_pipeline["step_oos"]
        gp1 = steps[steps["grid_point_id"] == "gp1"]
        assert list(gp1["step_status"]) == [StepStatus.OK.value] * 3

    def test_gp2_all_ok(self, golden_pipeline):
        steps = golden_pipeline["step_oos"]
        gp2 = steps[steps["grid_point_id"] == "gp2"]
        assert list(gp2["step_status"]) == [StepStatus.OK.value] * 3

    def test_gp3_all_gate_failed(self, golden_pipeline):
        steps = golden_pipeline["step_oos"]
        gp3 = steps[steps["grid_point_id"] == "gp3"]
        assert list(gp3["step_status"]) == [StepStatus.GATE_FAILED.value] * 3


# ===========================================================================
# Aggregation golden values
# ===========================================================================

class TestGoldenAggregation:
    """Exact numerical values from aggregation layer."""

    def test_gp1_n_ok_and_ok_ratio(self, golden_pipeline):
        row = _get_gp(golden_pipeline["aggregated"], "gp1")
        assert row["n_ok_steps"] == 3
        assert row["n_total_steps"] == 3
        assert row["ok_ratio"] == pytest.approx(1.0, abs=1e-9)

    def test_gp1_sum_pnl_pct(self, golden_pipeline):
        row = _get_gp(golden_pipeline["aggregated"], "gp1")
        assert row["sum_pnl_pct_Median"] == pytest.approx(10.0, abs=1e-9)
        assert row["sum_pnl_pct_Min"] == pytest.approx(8.0, abs=1e-9)
        assert row["sum_pnl_pct_Mean"] == pytest.approx(10.0, abs=1e-9)
        assert row["sum_pnl_pct_Std"] == pytest.approx(2.0, abs=1e-9)

    def test_gp1_profitable_segments_count(self, golden_pipeline):
        row = _get_gp(golden_pipeline["aggregated"], "gp1")
        assert row["profitable_segments_count"] == 3

    def test_gp2_sum_pnl_pct(self, golden_pipeline):
        row = _get_gp(golden_pipeline["aggregated"], "gp2")
        assert row["sum_pnl_pct_Median"] == pytest.approx(7.0, abs=1e-9)
        assert row["sum_pnl_pct_Min"] == pytest.approx(-2.0, abs=1e-9)
        assert row["sum_pnl_pct_Mean"] == pytest.approx(20.0 / 3.0, abs=1e-6)
        assert row["sum_pnl_pct_Std"] == pytest.approx(8.504900548, abs=1e-6)

    def test_gp2_profitable_segments_count(self, golden_pipeline):
        row = _get_gp(golden_pipeline["aggregated"], "gp2")
        assert row["profitable_segments_count"] == 2

    def test_gp2_max_drawdown_min(self, golden_pipeline):
        row = _get_gp(golden_pipeline["aggregated"], "gp2")
        assert row["max_drawdown_Min"] == pytest.approx(-0.15, abs=1e-9)

    def test_gp3_all_nan_due_to_gate_failed(self, golden_pipeline):
        row = _get_gp(golden_pipeline["aggregated"], "gp3")
        assert row["n_ok_steps"] == 0
        assert row["ok_ratio"] == pytest.approx(0.0, abs=1e-9)
        assert np.isnan(row["sum_pnl_pct_Median"])
        assert np.isnan(row["sum_pnl_pct_Mean"])
        assert row["profitable_segments_count"] == 0

    def test_gp1_num_trades_median(self, golden_pipeline):
        row = _get_gp(golden_pipeline["aggregated"], "gp1")
        assert row["num_trades_Median"] == pytest.approx(15.0, abs=1e-9)

    def test_gp1_max_drawdown_min(self, golden_pipeline):
        row = _get_gp(golden_pipeline["aggregated"], "gp1")
        assert row["max_drawdown_Min"] == pytest.approx(-0.08, abs=1e-9)


# ===========================================================================
# Candidate gates golden values
# ===========================================================================

class TestGoldenCandidateGates:
    """Exact gate results for each grid point."""

    def test_gp1_seed_gate_passed(self, golden_pipeline):
        row = _get_gp(golden_pipeline["gated"], "gp1")
        assert bool(row["seed_gate_passed"]) is True

    def test_gp2_seed_gate_passed(self, golden_pipeline):
        row = _get_gp(golden_pipeline["gated"], "gp2")
        assert bool(row["seed_gate_passed"]) is True

    def test_gp3_seed_gate_failed(self, golden_pipeline):
        row = _get_gp(golden_pipeline["gated"], "gp3")
        assert bool(row["seed_gate_passed"]) is False
        assert row["seed_gate_fail_reason"] == "no_ok_steps"


# ===========================================================================
# Scoring golden values
# ===========================================================================

class TestGoldenScoring:
    """Exact scores pinned to current formula."""

    def test_gp1_score(self, golden_pipeline):
        row = _get_gp(golden_pipeline["gated"], "gp1")
        assert row["tester_seed_score"] == pytest.approx(1.0, abs=1e-9)
        assert row["score_contract_status"] == "ok"

    def test_gp2_score(self, golden_pipeline):
        row = _get_gp(golden_pipeline["gated"], "gp2")
        assert row["tester_seed_score"] == pytest.approx(0.0, abs=1e-9)
        assert row["score_contract_status"] == "ok"

    def test_gp3_no_score(self, golden_pipeline):
        row = _get_gp(golden_pipeline["gated"], "gp3")
        assert np.isnan(row["tester_seed_score"])
        assert row["score_contract_status"] == "no_score"


# ===========================================================================
# Ranking golden values
# ===========================================================================

class TestGoldenRanking:
    """Exact rank order and tier assignments."""

    def test_rank_order(self, golden_pipeline):
        ranked = golden_pipeline["ranked"].sort_values("grid_rank")
        order = list(ranked["grid_point_id"])
        assert order == ["gp1", "gp2", "gp3"]

    def test_gp1_rank_and_tier(self, golden_pipeline):
        row = _get_gp(golden_pipeline["ranked"], "gp1")
        assert row["grid_rank"] == 1
        assert row["tier"] == 1

    def test_gp2_rank_and_tier(self, golden_pipeline):
        row = _get_gp(golden_pipeline["ranked"], "gp2")
        assert row["grid_rank"] == 2
        assert row["tier"] == 1

    def test_gp3_rank_and_tier(self, golden_pipeline):
        row = _get_gp(golden_pipeline["ranked"], "gp3")
        assert row["grid_rank"] == 3
        assert row["tier"] == 3

    def test_dense_rank_no_gaps(self, golden_pipeline):
        ranked = golden_pipeline["ranked"]
        assert sorted(ranked["grid_rank"].tolist()) == [1, 2, 3]
