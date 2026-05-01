"""
Tests for A10: Ranking (3-tier + tiebreakers)

Coverage per plan deliverables:
  Tier assignment (§7.1):
    - ok candidate + gate passed + enough segments → Tier 1
    - ok candidate + gate failed → Tier 2
    - partial candidate → Tier 2 (regardless of gates)
    - failed candidate (n_ok == 0) → Tier 3
    - ok + gate passed + insufficient ok steps → Tier 2
    - min_segments_for_ranking default: max(2, ceil(n*0.5))
    - min_segments_for_ranking explicit config override

  Sort (§7.2) gates_score mode:
    - Tier 1: tester_seed_score DESC → sum_pnl_pct_Median DESC → sum_pnl_pct_Std ASC → grid_point_id ASC
    - Tier 2: sum_pnl_pct_Median DESC → sum_pnl_pct_Min DESC → sum_pnl_pct_Std ASC → grid_point_id ASC
    - Tier 3: grid_point_id ASC
    - Tiers ordered: T1 before T2 before T3

  Sort (§7.2) legacy mode:
    - sort_by DESC → tiebreaker DESC → sum_pnl_pct_Std ASC → grid_point_id ASC

  Dense rank:
    - rank starts at 1, no gaps
    - grid_rank is first column

  Determinism:
    - same input → same output
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from wf_grid.config.schema import (
    DataConfig,
    GridConfig,
    RankingConfig,
)
from wf_grid.ranking.ranker import rank_candidates, _validate_sort_columns
from wf_grid.ranking.tiering import _resolve_min_segments, assign_tiers
from wf_grid.status.status_model import CandidateStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(
    mode: str = "gates_score",
    min_segments: int | None = None,
    sort_by: str = "sum_pnl_pct_Median",
    tiebreaker: str = "sum_pnl_pct_Min",
) -> GridConfig:
    return GridConfig(
        data=DataConfig(file_path="dummy.csv"),
        ranking=RankingConfig(
            mode=mode,
            min_segments_for_ranking=min_segments,
            sort_by=sort_by,
            tiebreaker=tiebreaker,
        ),
    )


def _row(
    gp_id: str,
    n_ok: int = 3,
    n_total: int = 3,
    gate_passed: bool = True,
    pnl_median: float = 10.0,
    pnl_min: float = 1.0,
    pnl_std: float = 2.0,
    seed_score: float = 0.8,
) -> dict:
    return {
        "grid_point_id": gp_id,
        "n_ok_steps": n_ok,
        "n_total_steps": n_total,
        "seed_gate_passed": gate_passed,
        "sum_pnl_pct_Median": pnl_median,
        "sum_pnl_pct_Min": pnl_min,
        "sum_pnl_pct_Std": pnl_std,
        "tester_seed_score": seed_score,
    }


def _df(*rows) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


# ===========================================================================
# Tier assignment (§7.1)
# ===========================================================================

class TestTierAssignment:
    def test_ok_gate_passed_enough_segments_is_tier1(self):
        df = _df(_row("gp1", n_ok=3, n_total=3, gate_passed=True))
        tiers = assign_tiers(df, _cfg())
        assert tiers.iloc[0] == 1

    def test_ok_gate_failed_is_tier2(self):
        df = _df(_row("gp1", n_ok=3, n_total=3, gate_passed=False))
        tiers = assign_tiers(df, _cfg())
        assert tiers.iloc[0] == 2

    def test_partial_always_tier2(self):
        df = _df(_row("gp1", n_ok=2, n_total=3, gate_passed=True))
        tiers = assign_tiers(df, _cfg())
        assert tiers.iloc[0] == 2

    def test_partial_gate_failed_tier2(self):
        df = _df(_row("gp1", n_ok=2, n_total=3, gate_passed=False))
        tiers = assign_tiers(df, _cfg())
        assert tiers.iloc[0] == 2

    def test_failed_is_tier3(self):
        df = _df(_row("gp1", n_ok=0, n_total=3, gate_passed=False))
        tiers = assign_tiers(df, _cfg())
        assert tiers.iloc[0] == 3

    def test_ok_gate_passed_insufficient_segments_is_tier2(self):
        # n_ok=1 but min_segments default = max(2, ceil(3*0.5)) = 2 → insufficient
        df = _df(_row("gp1", n_ok=1, n_total=3, gate_passed=True))
        tiers = assign_tiers(df, _cfg())
        assert tiers.iloc[0] == 2

    def test_min_segments_explicit_config(self):
        # explicit min_segments=1 → even n_ok=1 is enough for Tier 1
        df = _df(_row("gp1", n_ok=1, n_total=3, gate_passed=True))
        tiers = assign_tiers(df, _cfg(min_segments=1))
        # min_segments clamped to max(2, min(1, 3)) = 2 → still Tier 2
        assert tiers.iloc[0] == 2  # 1 < 2 (minimum enforced)

    def test_min_segments_default_formula(self):
        # n_segments=4 → default = max(2, ceil(4*0.5)) = 2
        from wf_grid.ranking.tiering import _resolve_min_segments
        cfg = _cfg()
        assert _resolve_min_segments(cfg, 4) == 2

    def test_min_segments_default_formula_large(self):
        # n_segments=10 → default = max(2, ceil(10*0.5)) = 5
        from wf_grid.ranking.tiering import _resolve_min_segments
        cfg = _cfg()
        assert _resolve_min_segments(cfg, 10) == 5

    def test_min_segments_default_formula_minimum(self):
        # n_segments=1 → default = max(2, ceil(1*0.5)) = max(2,1) = 2;
        # but clamped to n_segments=1 → 1
        from wf_grid.ranking.tiering import _resolve_min_segments
        cfg = _cfg()
        assert _resolve_min_segments(cfg, 1) == 1

    def test_mixed_tiers(self):
        df = _df(
            _row("gp1", n_ok=3, n_total=3, gate_passed=True),   # Tier 1
            _row("gp2", n_ok=2, n_total=3, gate_passed=True),   # Tier 2 (partial)
            _row("gp3", n_ok=0, n_total=3, gate_passed=False),  # Tier 3
        )
        tiers = assign_tiers(df, _cfg())
        assert list(tiers) == [1, 2, 3]


# ===========================================================================
# Dense rank + structure
# ===========================================================================

class TestDenseRank:
    def test_grid_rank_starts_at_one(self):
        df = _df(
            _row("gp1", n_ok=3, n_total=3, gate_passed=True),
            _row("gp2", n_ok=2, n_total=3, gate_passed=True),
        )
        result = rank_candidates(df, _cfg())
        assert result["grid_rank"].min() == 1

    def test_grid_rank_no_gaps(self):
        df = _df(
            _row("gp1", n_ok=3, n_total=3, gate_passed=True),
            _row("gp2", n_ok=2, n_total=3, gate_passed=True),
            _row("gp3", n_ok=0, n_total=3, gate_passed=False),
        )
        result = rank_candidates(df, _cfg())
        assert sorted(result["grid_rank"].tolist()) == [1, 2, 3]

    def test_grid_rank_is_first_column(self):
        df = _df(_row("gp1"))
        result = rank_candidates(df, _cfg())
        assert result.columns[0] == "grid_rank"

    def test_tier_column_present(self):
        df = _df(_row("gp1"))
        result = rank_candidates(df, _cfg())
        assert "tier" in result.columns

    def test_empty_df(self):
        result = rank_candidates(pd.DataFrame(), _cfg())
        assert "grid_rank" in result.columns
        assert len(result) == 0


# ===========================================================================
# Tiers ordered T1 → T2 → T3
# ===========================================================================

class TestTierOrdering:
    def test_tier1_before_tier2_before_tier3(self):
        df = _df(
            _row("gp3", n_ok=0, n_total=3, gate_passed=False),  # Tier 3
            _row("gp2", n_ok=2, n_total=3, gate_passed=True),   # Tier 2
            _row("gp1", n_ok=3, n_total=3, gate_passed=True),   # Tier 1
        )
        result = rank_candidates(df, _cfg())
        tier_order = result.sort_values("grid_rank")["tier"].tolist()
        # Tier 1 should have rank 1, Tier 3 should have rank 3
        assert result[result["tier"] == 1]["grid_rank"].iloc[0] == 1
        assert result[result["tier"] == 3]["grid_rank"].iloc[0] == 3


# ===========================================================================
# Sort: gates_score mode (§7.2)
# ===========================================================================

class TestSortGatesScore:
    def test_tier1_sorted_by_seed_score_desc(self):
        df = _df(
            _row("gp1", n_ok=3, n_total=3, gate_passed=True, seed_score=0.5),
            _row("gp2", n_ok=3, n_total=3, gate_passed=True, seed_score=0.9),
            _row("gp3", n_ok=3, n_total=3, gate_passed=True, seed_score=0.7),
        )
        result = rank_candidates(df, _cfg())
        t1 = result[result["tier"] == 1].sort_values("grid_rank")
        assert list(t1["grid_point_id"]) == ["gp2", "gp3", "gp1"]

    def test_tier1_tiebreaker_pnl_median_desc(self):
        df = _df(
            _row("gp1", n_ok=3, n_total=3, gate_passed=True, seed_score=0.8, pnl_median=10.0),
            _row("gp2", n_ok=3, n_total=3, gate_passed=True, seed_score=0.8, pnl_median=20.0),
        )
        result = rank_candidates(df, _cfg())
        t1 = result[result["tier"] == 1].sort_values("grid_rank")
        assert t1.iloc[0]["grid_point_id"] == "gp2"

    def test_tier1_tiebreaker_pnl_std_asc(self):
        df = _df(
            _row("gp1", n_ok=3, n_total=3, gate_passed=True, seed_score=0.8, pnl_median=10.0, pnl_std=5.0),
            _row("gp2", n_ok=3, n_total=3, gate_passed=True, seed_score=0.8, pnl_median=10.0, pnl_std=2.0),
        )
        result = rank_candidates(df, _cfg())
        t1 = result[result["tier"] == 1].sort_values("grid_rank")
        # Lower std is better → gp2 first
        assert t1.iloc[0]["grid_point_id"] == "gp2"

    def test_tier1_final_tiebreaker_gp_id_asc(self):
        df = _df(
            _row("gp_b", n_ok=3, n_total=3, gate_passed=True, seed_score=0.8, pnl_median=10.0, pnl_std=2.0),
            _row("gp_a", n_ok=3, n_total=3, gate_passed=True, seed_score=0.8, pnl_median=10.0, pnl_std=2.0),
        )
        result = rank_candidates(df, _cfg())
        t1 = result[result["tier"] == 1].sort_values("grid_rank")
        # grid_point_id ASC → gp_a before gp_b
        assert t1.iloc[0]["grid_point_id"] == "gp_a"

    def test_tier2_sorted_by_pnl_median_desc(self):
        df = _df(
            _row("gp1", n_ok=2, n_total=3, gate_passed=True, pnl_median=5.0),
            _row("gp2", n_ok=2, n_total=3, gate_passed=True, pnl_median=15.0),
        )
        result = rank_candidates(df, _cfg())
        t2 = result[result["tier"] == 2].sort_values("grid_rank")
        assert t2.iloc[0]["grid_point_id"] == "gp2"

    def test_tier3_sorted_by_gp_id_asc(self):
        df = _df(
            _row("gp_z", n_ok=0, n_total=3, gate_passed=False),
            _row("gp_a", n_ok=0, n_total=3, gate_passed=False),
        )
        result = rank_candidates(df, _cfg())
        t3 = result[result["tier"] == 3].sort_values("grid_rank")
        assert t3.iloc[0]["grid_point_id"] == "gp_a"


# ===========================================================================
# Sort: legacy mode (§7.2)
# ===========================================================================

class TestSortLegacy:
    def test_legacy_sort_by_desc(self):
        df = _df(
            _row("gp1", n_ok=3, n_total=3, gate_passed=True, pnl_median=5.0),
            _row("gp2", n_ok=3, n_total=3, gate_passed=True, pnl_median=15.0),
        )
        result = rank_candidates(df, _cfg(mode="legacy"))
        assert result.iloc[0]["grid_point_id"] == "gp2"

    def test_legacy_tiebreaker_desc(self):
        df = _df(
            _row("gp1", n_ok=3, n_total=3, gate_passed=True, pnl_median=10.0, pnl_min=1.0),
            _row("gp2", n_ok=3, n_total=3, gate_passed=True, pnl_median=10.0, pnl_min=5.0),
        )
        result = rank_candidates(df, _cfg(mode="legacy"))
        assert result.iloc[0]["grid_point_id"] == "gp2"

    def test_legacy_tiebreaker_gp_id_final(self):
        df = _df(
            _row("gp_b", n_ok=3, n_total=3, gate_passed=True, pnl_median=10.0, pnl_min=1.0, pnl_std=2.0),
            _row("gp_a", n_ok=3, n_total=3, gate_passed=True, pnl_median=10.0, pnl_min=1.0, pnl_std=2.0),
        )
        result = rank_candidates(df, _cfg(mode="legacy"))
        assert result.iloc[0]["grid_point_id"] == "gp_a"

    def test_legacy_tiers_still_assigned(self):
        df = _df(
            _row("gp1", n_ok=3, n_total=3, gate_passed=True),
            _row("gp2", n_ok=0, n_total=3, gate_passed=False),
        )
        result = rank_candidates(df, _cfg(mode="legacy"))
        assert "tier" in result.columns
        tiers = result.set_index("grid_point_id")["tier"]
        assert tiers["gp1"] == 1
        assert tiers["gp2"] == 3

    # FIX-2.7 — tier must NOT affect sort order in legacy mode
    def test_legacy_tier2_beats_tier1_when_better_sort_by(self):
        """FIX-2.7: Tier 2 candidate with higher sort_by must rank above Tier 1."""
        df = _df(
            # gp1: Tier 1 (ok + gate passed), but lower pnl
            _row("gp1", n_ok=3, n_total=3, gate_passed=True,  pnl_median=5.0),
            # gp2: Tier 2 (partial), but higher pnl
            _row("gp2", n_ok=2, n_total=3, gate_passed=True,  pnl_median=20.0),
        )
        result = rank_candidates(df, _cfg(mode="legacy", sort_by="sum_pnl_pct_Median"))
        # In legacy mode sort_by dominates — gp2 must be rank 1
        assert result[result["grid_point_id"] == "gp2"]["grid_rank"].iloc[0] == 1
        assert result[result["grid_point_id"] == "gp1"]["grid_rank"].iloc[0] == 2

    def test_legacy_gate_passed_ranks_above_failed_regardless_of_metric(self):
        """In legacy mode seed_gate_passed is primary sort key.

        A gate-passed candidate must rank above a gate-failed one even if
        the failed candidate has a higher sort_by metric value.
        This prevents survivorship-bias artifacts from reaching rank-1.
        """
        df = _df(
            _row("gp1", n_ok=3, n_total=3, gate_passed=True,  pnl_median=5.0),
            _row("gp2", n_ok=0, n_total=3, gate_passed=False, pnl_median=99.0),
        )
        result = rank_candidates(df, _cfg(mode="legacy", sort_by="sum_pnl_pct_Median"))
        assert result[result["grid_point_id"] == "gp1"]["grid_rank"].iloc[0] == 1
        assert result[result["grid_point_id"] == "gp2"]["grid_rank"].iloc[0] == 2

    def test_gates_score_mode_tier_still_dominates(self):
        """Regression: in gates_score mode Tier 1 must always beat Tier 2 even with worse metrics."""
        df = _df(
            # gp1: Tier 1, lower pnl
            _row("gp1", n_ok=3, n_total=3, gate_passed=True,  pnl_median=5.0,  seed_score=0.5),
            # gp2: Tier 2 (partial), higher pnl
            _row("gp2", n_ok=2, n_total=3, gate_passed=True,  pnl_median=20.0, seed_score=0.9),
        )
        result = rank_candidates(df, _cfg(mode="gates_score"))
        # In gates_score mode Tier 1 always comes before Tier 2
        assert result[result["grid_point_id"] == "gp1"]["grid_rank"].iloc[0] == 1
        assert result[result["grid_point_id"] == "gp2"]["grid_rank"].iloc[0] == 2


# ===========================================================================
# Determinism
# ===========================================================================

class TestDeterminism:
    def test_same_input_same_output(self):
        df = _df(
            _row("gp1", n_ok=3, n_total=3, gate_passed=True, seed_score=0.9),
            _row("gp2", n_ok=2, n_total=3, gate_passed=True, seed_score=0.5),
            _row("gp3", n_ok=0, n_total=3, gate_passed=False),
        )
        r1 = rank_candidates(df, _cfg())
        r2 = rank_candidates(df, _cfg())
        pd.testing.assert_frame_equal(
            r1.reset_index(drop=True),
            r2.reset_index(drop=True),
        )


# ===========================================================================
# FIX-2.2 — sort_by / tiebreaker column existence validation
# ===========================================================================

class TestSortByColumnValidation:
    """FIX-2.2: Typo in sort_by/tiebreaker → explicit ValueError, not silent fallback."""

    def test_invalid_sort_by_raises_value_error(self):
        df = _df(_row("gp1", n_ok=3, n_total=3, gate_passed=True))
        config = _cfg(mode="legacy", sort_by="nonexistent_column")
        with pytest.raises(ValueError, match="sort_by"):
            rank_candidates(df, config)

    def test_invalid_tiebreaker_raises_value_error(self):
        df = _df(_row("gp1", n_ok=3, n_total=3, gate_passed=True))
        config = _cfg(mode="legacy", tiebreaker="nonexistent_column")
        with pytest.raises(ValueError, match="tiebreaker"):
            rank_candidates(df, config)

    def test_error_message_contains_column_name(self):
        df = _df(_row("gp1", n_ok=3, n_total=3, gate_passed=True))
        config = _cfg(mode="legacy", sort_by="typo_column")
        with pytest.raises(ValueError, match="typo_column"):
            rank_candidates(df, config)

    def test_error_message_contains_available_columns(self):
        df = _df(_row("gp1", n_ok=3, n_total=3, gate_passed=True))
        config = _cfg(mode="legacy", sort_by="nonexistent_column")
        with pytest.raises(ValueError, match="sum_pnl_pct_Median"):
            rank_candidates(df, config)

    def test_valid_sort_by_no_error(self):
        """Regression: correct column names → no error."""
        df = _df(
            _row("gp1", n_ok=3, n_total=3, gate_passed=True, pnl_median=10.0),
            _row("gp2", n_ok=3, n_total=3, gate_passed=True, pnl_median=5.0),
        )
        config = _cfg(mode="legacy", sort_by="sum_pnl_pct_Median",
                      tiebreaker="sum_pnl_pct_Min")
        result = rank_candidates(df, config)
        assert result.iloc[0]["grid_point_id"] == "gp1"

    def test_validation_only_in_legacy_mode(self):
        """gates_score mode must NOT raise even if sort_by column missing."""
        df = _df(_row("gp1", n_ok=3, n_total=3, gate_passed=True))
        config = _cfg(mode="gates_score", sort_by="nonexistent_column")
        # Should not raise — gates_score ignores sort_by config
        result = rank_candidates(df, config)
        assert len(result) == 1

    def test_validate_sort_columns_direct_sort_by(self):
        """Unit test _validate_sort_columns directly — sort_by missing."""
        df = _df(_row("gp1"))
        config = _cfg(mode="legacy", sort_by="missing_col")
        with pytest.raises(ValueError, match="missing_col"):
            _validate_sort_columns(df, config)

    def test_validate_sort_columns_direct_tiebreaker(self):
        """Unit test _validate_sort_columns directly — tiebreaker missing."""
        df = _df(_row("gp1"))
        config = _cfg(mode="legacy", tiebreaker="missing_tb")
        with pytest.raises(ValueError, match="missing_tb"):
            _validate_sort_columns(df, config)


# ===========================================================================
# seed_gate_passed required for rank_candidates
# ===========================================================================

class TestSeedGatePassedRequired:
    def test_missing_seed_gate_passed_raises_value_error(self):
        """Non-empty input without seed_gate_passed must fail fast with clear contract."""
        df = pd.DataFrame(
            [
                {
                    "grid_point_id": "gp1",
                    "n_ok_steps": 3,
                    "n_total_steps": 3,
                    "sum_pnl_pct_Median": 10.0,
                    "sum_pnl_pct_Min": 1.0,
                    "sum_pnl_pct_Std": 2.0,
                    "tester_seed_score": 0.8,
                },
            ],
        )
        with pytest.raises(ValueError, match="seed_gate_passed"):
            rank_candidates(df, _cfg(mode="legacy"))
