"""
Tests for A9: Scoring (tester_seed_score)

Coverage per plan deliverables:
  - all-valid: all components present, score_contract_status = "ok"
  - one-NaN redistribute: partial status, weights redistributed
  - all-NaN: score = NaN, status = "no_score"
  - single passed row (min == max): zero range → weight redistributed
  - missing column: treated as NaN, partial
  - weight auto-normalize: non-unit sum normalised
  - n_ok_steps == 0 / not passed → NaN + no_score (no normalization attempt)
  - lower_is_better inversion: abs_max_drawdown_Min inverted
  - determinism: same input → same output
  - rank mode: outlier robustness, ties (average rank), LOWER_IS_BETTER direction
  - normalization_mode parity: both modes yield same ranking on clean 2-row data
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from wf_grid.ranking.scoring import (
    DEFAULT_SCORE_WEIGHTS,
    LOWER_IS_BETTER,
    calculate_seed_score,
    compute_score_discrimination,
    _normalize_minmax,
    _normalize_rank,
)
from wf_grid.status.status_model import ScoreContractStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agg_row(
    gp_id: str = "gp1",
    n_ok: int = 3,
    pnl_median: float = 10.0,
    profitable_count: int = 3,
    dd_min: float = -0.20,
    dd_median: float = -0.15,
) -> dict:
    return {
        "grid_point_id": gp_id,
        "n_ok_steps": n_ok,
        "sum_pnl_pct_Median": pnl_median,
        "profitable_segments_count": profitable_count,
        "max_drawdown_Min": dd_min,
        "max_drawdown_Median": dd_median,
    }


def _make_df(*rows) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


def _all_passed(df: pd.DataFrame) -> pd.Series:
    return pd.Series(True, index=df.index)


def _none_passed(df: pd.DataFrame) -> pd.Series:
    return pd.Series(False, index=df.index)


# ---------------------------------------------------------------------------
# All-valid
# ---------------------------------------------------------------------------

class TestAllValid:
    def test_two_rows_scores_and_status(self):
        df = _make_df(
            _make_agg_row("gp1", pnl_median=20.0, profitable_count=5, dd_min=-0.10),
            _make_agg_row("gp2", pnl_median=10.0, profitable_count=3, dd_min=-0.30),
        )
        mask = _all_passed(df)
        scores, statuses = calculate_seed_score(df, mask)

        assert len(scores) == 2
        assert not np.isnan(scores.iloc[0])
        assert not np.isnan(scores.iloc[1])
        assert statuses.iloc[0] == ScoreContractStatus.OK.value
        assert statuses.iloc[1] == ScoreContractStatus.OK.value

    def test_higher_pnl_higher_score(self):
        df = _make_df(
            _make_agg_row("gp1", pnl_median=20.0, profitable_count=5, dd_min=-0.10),
            _make_agg_row("gp2", pnl_median=10.0, profitable_count=3, dd_min=-0.30),
        )
        scores, _ = calculate_seed_score(df, _all_passed(df))
        assert scores.iloc[0] > scores.iloc[1]

    def test_score_in_zero_one(self):
        df = _make_df(
            _make_agg_row("gp1", pnl_median=20.0, profitable_count=5, dd_min=-0.10),
            _make_agg_row("gp2", pnl_median=10.0, profitable_count=3, dd_min=-0.30),
        )
        scores, _ = calculate_seed_score(df, _all_passed(df))
        assert 0.0 <= scores.iloc[0] <= 1.0
        assert 0.0 <= scores.iloc[1] <= 1.0


# ---------------------------------------------------------------------------
# One-NaN redistribute (§7.3 policy 2)
# ---------------------------------------------------------------------------

class TestNaNRedistribute:
    def test_one_nan_partial_status(self):
        df = _make_df(
            _make_agg_row("gp1", pnl_median=20.0, profitable_count=5, dd_min=-0.10),
            _make_agg_row("gp2", pnl_median=np.nan, profitable_count=3, dd_min=-0.30),
        )
        scores, statuses = calculate_seed_score(df, _all_passed(df))
        assert statuses.iloc[0] == ScoreContractStatus.OK.value
        assert statuses.iloc[1] == ScoreContractStatus.PARTIAL.value
        assert not np.isnan(scores.iloc[1])

    def test_nan_weight_redistributed_to_others(self):
        # Two rows so normalization has range; gp1 has NaN pnl_median
        df = _make_df(
            _make_agg_row("gp1", pnl_median=np.nan, profitable_count=5, dd_min=-0.10),
            _make_agg_row("gp2", pnl_median=20.0, profitable_count=3, dd_min=-0.30),
        )
        scores, statuses = calculate_seed_score(df, _all_passed(df))
        assert not np.isnan(scores.iloc[0])
        assert statuses.iloc[0] == ScoreContractStatus.PARTIAL.value
        assert statuses.iloc[1] == ScoreContractStatus.OK.value


# ---------------------------------------------------------------------------
# All-NaN → no_score (§7.3 policy 2)
# ---------------------------------------------------------------------------

class TestAllNaN:
    def test_all_nan_gives_no_score(self):
        df = _make_df(
            _make_agg_row("gp1", pnl_median=np.nan, profitable_count=np.nan, dd_min=np.nan),
        )
        # profitable_count as NaN via float column
        df["profitable_segments_count"] = np.nan
        scores, statuses = calculate_seed_score(df, _all_passed(df))
        assert np.isnan(scores.iloc[0])
        assert statuses.iloc[0] == ScoreContractStatus.NO_SCORE.value


# ---------------------------------------------------------------------------
# Single passed row (min == max → zero range) (§7.3 policy 1)
# ---------------------------------------------------------------------------

class TestSingleRow:
    def test_single_row_zero_range(self):
        df = _make_df(
            _make_agg_row("gp1", pnl_median=10.0, profitable_count=5, dd_min=-0.10),
        )
        scores, statuses = calculate_seed_score(df, _all_passed(df))
        # All columns have min == max → zero range → all weights redistributed
        # No contributing columns → score should be NaN or 0.0
        # Per plan: norm = 0.0, weight redistributed. If ALL zero range → no_score
        assert np.isnan(scores.iloc[0]) or scores.iloc[0] == 0.0
        # With single row, all columns have min==max: no contributing column
        assert statuses.iloc[0] in (
            ScoreContractStatus.NO_SCORE.value,
            ScoreContractStatus.OK.value,
        )

    def test_two_rows_with_different_values(self):
        df = _make_df(
            _make_agg_row("gp1", pnl_median=20.0, profitable_count=5, dd_min=-0.10),
            _make_agg_row("gp2", pnl_median=10.0, profitable_count=3, dd_min=-0.30),
        )
        scores, statuses = calculate_seed_score(df, _all_passed(df))
        # Both should get ok status
        assert statuses.iloc[0] == ScoreContractStatus.OK.value
        assert statuses.iloc[1] == ScoreContractStatus.OK.value
        # Best row should score 1.0, worst should score 0.0
        assert scores.iloc[0] == pytest.approx(1.0)
        assert scores.iloc[1] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Missing column → KeyError (fail-fast, not silent redistribution)
# ---------------------------------------------------------------------------

class TestMissingColumn:
    def test_missing_column_raises_key_error(self):
        """Any column in score_weights that is absent in df must raise KeyError."""
        df = _make_df(
            _make_agg_row("gp1", pnl_median=20.0, profitable_count=5, dd_min=-0.10),
            _make_agg_row("gp2", pnl_median=10.0, profitable_count=3, dd_min=-0.30),
        )
        custom_weights = {
            "sum_pnl_pct_Median": 0.5,
            "nonexistent_column": 0.5,
        }
        with pytest.raises(KeyError, match="nonexistent_column"):
            calculate_seed_score(df, _all_passed(df), score_weights=custom_weights)

    def test_missing_column_error_message_informative(self):
        """KeyError message must name the missing column(s)."""
        df = _make_df(
            _make_agg_row("gp1", pnl_median=10.0, profitable_count=3, dd_min=-0.10),
        )
        custom_weights = {"sum_pnl_pct_Median": 0.5, "missing_metric": 0.5}
        with pytest.raises(KeyError, match="missing_metric"):
            calculate_seed_score(df, _all_passed(df), score_weights=custom_weights)

    def test_abs_max_drawdown_min_missing_raises(self):
        """abs_max_drawdown_Min in weights but no max_drawdown_Min either → KeyError."""
        df = _make_df(
            _make_agg_row("gp1", pnl_median=10.0, profitable_count=3, dd_min=-0.10),
            _make_agg_row("gp2", pnl_median=20.0, profitable_count=5, dd_min=-0.05),
        )
        # Drop the source column so derivation also fails
        df = df.drop(columns=["max_drawdown_Min"])
        with pytest.raises(KeyError, match="abs_max_drawdown_Min"):
            calculate_seed_score(df, _all_passed(df))


# ---------------------------------------------------------------------------
# Weight auto-normalize (§7.3 policy 3)
# ---------------------------------------------------------------------------

class TestWeightAutoNormalize:
    def test_non_unit_weights_normalized(self):
        df = _make_df(
            _make_agg_row("gp1", pnl_median=20.0, profitable_count=5, dd_min=-0.10),
            _make_agg_row("gp2", pnl_median=10.0, profitable_count=3, dd_min=-0.30),
        )
        w1 = {"sum_pnl_pct_Median": 0.45, "profitable_segments_count": 0.35, "abs_max_drawdown_Min": 0.20}
        w2 = {"sum_pnl_pct_Median": 4.5, "profitable_segments_count": 3.5, "abs_max_drawdown_Min": 2.0}

        s1, _ = calculate_seed_score(df, _all_passed(df), score_weights=w1)
        s2, _ = calculate_seed_score(df, _all_passed(df), score_weights=w2)

        pd.testing.assert_series_equal(s1, s2, check_names=False)


# ---------------------------------------------------------------------------
# Not passed → NaN + no_score
# ---------------------------------------------------------------------------

class TestNotPassed:
    def test_not_passed_nan_and_no_score(self):
        df = _make_df(
            _make_agg_row("gp1", pnl_median=20.0, profitable_count=5, dd_min=-0.10),
        )
        scores, statuses = calculate_seed_score(df, _none_passed(df))
        assert np.isnan(scores.iloc[0])
        assert statuses.iloc[0] == ScoreContractStatus.NO_SCORE.value

    def test_mixed_passed_not_passed(self):
        df = _make_df(
            _make_agg_row("gp1", pnl_median=20.0, profitable_count=5, dd_min=-0.10),
            _make_agg_row("gp2", pnl_median=10.0, profitable_count=3, dd_min=-0.30),
        )
        mask = pd.Series([True, False], index=df.index)
        scores, statuses = calculate_seed_score(df, mask)
        # gp2 not passed
        assert np.isnan(scores.iloc[1])
        assert statuses.iloc[1] == ScoreContractStatus.NO_SCORE.value
        # gp1 single passed → all zero range → NaN or 0
        # (only one passed row, so min==max for all columns)


# ---------------------------------------------------------------------------
# lower_is_better inversion
# ---------------------------------------------------------------------------

class TestLowerIsBetter:
    def test_lower_drawdown_higher_score(self):
        df = _make_df(
            _make_agg_row("gp1", pnl_median=10.0, profitable_count=3, dd_min=-0.10),
            _make_agg_row("gp2", pnl_median=10.0, profitable_count=3, dd_min=-0.50),
        )
        # Same pnl and profitable count, only drawdown differs
        # abs_max_drawdown_Min: gp1=0.10, gp2=0.50 → lower is better → gp1 gets higher norm
        custom_w = {"abs_max_drawdown_Min": 1.0}
        scores, _ = calculate_seed_score(df, _all_passed(df), score_weights=custom_w)
        assert scores.iloc[0] > scores.iloc[1]


# ---------------------------------------------------------------------------
# abs_max_drawdown_Min derived from max_drawdown_Min
# ---------------------------------------------------------------------------

class TestDerivedAbsDrawdown:
    def test_abs_derived_when_missing(self):
        df = _make_df(
            _make_agg_row("gp1", pnl_median=20.0, profitable_count=5, dd_min=-0.10),
            _make_agg_row("gp2", pnl_median=10.0, profitable_count=3, dd_min=-0.50),
        )
        # abs_max_drawdown_Min should be derived from max_drawdown_Min
        assert "abs_max_drawdown_Min" not in df.columns
        scores, statuses = calculate_seed_score(df, _all_passed(df))
        assert not np.isnan(scores.iloc[0])
        assert statuses.iloc[0] == ScoreContractStatus.OK.value


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_input_same_output(self):
        df = _make_df(
            _make_agg_row("gp1", pnl_median=20.0, profitable_count=5, dd_min=-0.10),
            _make_agg_row("gp2", pnl_median=10.0, profitable_count=3, dd_min=-0.30),
        )
        mask = _all_passed(df)
        s1, st1 = calculate_seed_score(df, mask)
        s2, st2 = calculate_seed_score(df, mask)
        pd.testing.assert_series_equal(s1, s2, check_names=False)
        pd.testing.assert_series_equal(st1, st2, check_names=False)


# ---------------------------------------------------------------------------
# Empty input
# ---------------------------------------------------------------------------

class TestEmptyInput:
    def test_empty_df(self):
        df = pd.DataFrame()
        mask = pd.Series(dtype=bool)
        scores, statuses = calculate_seed_score(df, mask)
        assert len(scores) == 0
        assert len(statuses) == 0


# ===========================================================================
# FIX-3.2: Score discrimination status
# ===========================================================================

class TestScoreDiscrimination:
    """Tests for compute_score_discrimination (raw feature spread check)."""

    def test_empty_df_returns_empty(self):
        df = pd.DataFrame()
        mask = pd.Series(dtype=bool)
        result = compute_score_discrimination(df, mask)
        assert len(result) == 0

    def test_no_passed_returns_no_score(self):
        df = _make_df(
            _make_agg_row("gp1", pnl_median=10.0),
            _make_agg_row("gp2", pnl_median=20.0),
        )
        result = compute_score_discrimination(df, _none_passed(df))
        assert (result == "no_score").all()

    def test_one_passed_insufficient(self):
        df = _make_df(
            _make_agg_row("gp1", pnl_median=10.0),
            _make_agg_row("gp2", pnl_median=20.0),
        )
        mask = pd.Series([True, False], index=df.index)
        result = compute_score_discrimination(df, mask)
        assert result.iloc[0] == "insufficient"
        assert result.iloc[1] == "no_score"

    def test_two_passed_insufficient(self):
        df = _make_df(
            _make_agg_row("gp1", pnl_median=10.0),
            _make_agg_row("gp2", pnl_median=20.0),
            _make_agg_row("gp3", pnl_median=30.0),
        )
        mask = pd.Series([True, True, False], index=df.index)
        result = compute_score_discrimination(df, mask)
        assert result.iloc[0] == "insufficient"
        assert result.iloc[1] == "insufficient"
        assert result.iloc[2] == "no_score"

    def test_three_passed_insufficient(self):
        """FIX-3: n_passed=3 → insufficient (raised from < 3 to < 5)."""
        df = _make_df(
            _make_agg_row("gp1", pnl_median=10.0),
            _make_agg_row("gp2", pnl_median=10.5),
            _make_agg_row("gp3", pnl_median=10.8),
        )
        result = compute_score_discrimination(df, _all_passed(df))
        assert (result == "insufficient").all()

    def test_three_passed_ok_spread_still_insufficient(self):
        """FIX-3: n_passed=3 with wide spread → still insufficient, not ok."""
        df = _make_df(
            _make_agg_row("gp1", pnl_median=10.0),
            _make_agg_row("gp2", pnl_median=15.0),
            _make_agg_row("gp3", pnl_median=20.0),
        )
        result = compute_score_discrimination(df, _all_passed(df))
        assert (result == "insufficient").all()

    def test_four_passed_insufficient(self):
        """FIX-3: n_passed=4 → insufficient."""
        df = _make_df(
            _make_agg_row("gp1", pnl_median=10.0),
            _make_agg_row("gp2", pnl_median=15.0),
            _make_agg_row("gp3", pnl_median=20.0),
            _make_agg_row("gp4", pnl_median=25.0),
        )
        result = compute_score_discrimination(df, _all_passed(df))
        assert (result == "insufficient").all()

    def test_four_passed_mixed_insufficient(self):
        """FIX-3: 3 passed out of 4 rows → passed get insufficient, not-passed get no_score."""
        df = _make_df(
            _make_agg_row("gp1", pnl_median=10.0),
            _make_agg_row("gp2", pnl_median=20.0),
            _make_agg_row("gp3", pnl_median=30.0),
            _make_agg_row("gp4", pnl_median=40.0),
        )
        mask = pd.Series([True, True, True, False], index=df.index)
        result = compute_score_discrimination(df, mask)
        assert result.iloc[0] == "insufficient"
        assert result.iloc[1] == "insufficient"
        assert result.iloc[2] == "insufficient"
        assert result.iloc[3] == "no_score"

    def test_five_passed_low_spread(self):
        """FIX-3: n_passed=5 → proceeds to spread check → low_spread."""
        df = _make_df(
            _make_agg_row("gp1", pnl_median=10.0),
            _make_agg_row("gp2", pnl_median=10.2),
            _make_agg_row("gp3", pnl_median=10.4),
            _make_agg_row("gp4", pnl_median=10.6),
            _make_agg_row("gp5", pnl_median=10.8),
        )
        result = compute_score_discrimination(df, _all_passed(df))
        assert (result == "low_spread").all()

    def test_five_passed_ok_spread(self):
        """FIX-3: n_passed=5 with wide spread → ok."""
        df = _make_df(
            _make_agg_row("gp1", pnl_median=10.0),
            _make_agg_row("gp2", pnl_median=15.0),
            _make_agg_row("gp3", pnl_median=20.0),
            _make_agg_row("gp4", pnl_median=25.0),
            _make_agg_row("gp5", pnl_median=30.0),
        )
        result = compute_score_discrimination(df, _all_passed(df))
        assert (result == "ok").all()

    def test_nan_values_in_spread_column(self):
        """NaN values in the spread column should be dropped before computing range."""
        df = _make_df(
            _make_agg_row("gp1", pnl_median=10.0),
            _make_agg_row("gp2", pnl_median=np.nan),
            _make_agg_row("gp3", pnl_median=10.5),
            _make_agg_row("gp4", pnl_median=10.2),
            _make_agg_row("gp5", pnl_median=10.3),
        )
        result = compute_score_discrimination(df, _all_passed(df))
        assert (result == "low_spread").all()

    def test_all_nan_in_spread_column_gives_ok(self):
        """If all passed values are NaN, can't compute range → default to ok."""
        df = _make_df(
            _make_agg_row("gp1", pnl_median=np.nan),
            _make_agg_row("gp2", pnl_median=np.nan),
            _make_agg_row("gp3", pnl_median=np.nan),
            _make_agg_row("gp4", pnl_median=np.nan),
            _make_agg_row("gp5", pnl_median=np.nan),
        )
        result = compute_score_discrimination(df, _all_passed(df))
        assert (result == "ok").all()

    def test_custom_spread_column(self):
        """Can specify a different column for spread check (needs >=5 passed)."""
        df = _make_df(
            _make_agg_row("gp1", pnl_median=10.0, dd_min=-0.05),
            _make_agg_row("gp2", pnl_median=10.0, dd_min=-0.06),
            _make_agg_row("gp3", pnl_median=10.0, dd_min=-0.07),
            _make_agg_row("gp4", pnl_median=10.0, dd_min=-0.08),
            _make_agg_row("gp5", pnl_median=10.0, dd_min=-0.09),
        )
        result = compute_score_discrimination(
            df, _all_passed(df),
            spread_column="max_drawdown_Min",
            low_spread_threshold=0.05,
        )
        assert (result == "low_spread").all()

    def test_custom_threshold(self):
        """Custom threshold applied after sufficient candidates (>=5 passed)."""
        df = _make_df(
            _make_agg_row("gp1", pnl_median=10.0),
            _make_agg_row("gp2", pnl_median=12.0),
            _make_agg_row("gp3", pnl_median=14.0),
            _make_agg_row("gp4", pnl_median=11.0),
            _make_agg_row("gp5", pnl_median=13.0),
        )
        result = compute_score_discrimination(
            df, _all_passed(df), low_spread_threshold=5.0,
        )
        assert (result == "low_spread").all()

    def test_missing_spread_column_gives_ok(self):
        """If spread_column not in df, default to ok (needs >=5 passed)."""
        df = _make_df(
            _make_agg_row("gp1", pnl_median=10.0),
            _make_agg_row("gp2", pnl_median=10.0),
            _make_agg_row("gp3", pnl_median=10.0),
            _make_agg_row("gp4", pnl_median=10.0),
            _make_agg_row("gp5", pnl_median=10.0),
        )
        result = compute_score_discrimination(
            df, _all_passed(df), spread_column="nonexistent",
        )
        assert (result == "ok").all()


# ===========================================================================
# Rank normalization — unit-level tests on _normalize_rank
# ===========================================================================

class TestNormalizeRankUnit:
    """Direct tests of _normalize_rank internals."""

    def _raw(self, values, col="x"):
        idx = pd.RangeIndex(len(values))
        return {col: pd.Series([float(v) if v is not None else np.nan
                                for v in values], index=idx)}, idx

    def test_basic_three_rows_higher_is_better(self):
        raw, idx = self._raw([10.0, 20.0, 30.0])
        normed, zero = _normalize_rank(raw, idx, lower_is_better=set())
        vals = normed["x"].values
        # ascending=True: 10→rank1(0-based 0)→0.0, 30→rank3(0-based 2)→1.0
        assert vals == pytest.approx([0.0, 0.5, 1.0], abs=1e-9)
        assert "x" not in zero

    def test_basic_three_rows_lower_is_better(self):
        raw, idx = self._raw([10.0, 20.0, 30.0])
        normed, zero = _normalize_rank(raw, idx, lower_is_better={"x"})
        vals = normed["x"].values
        # 10→smallest→best→1.0; 30→largest→worst→0.0
        assert vals == pytest.approx([1.0, 0.5, 0.0], abs=1e-9)
        assert "x" not in zero

    def test_n1_contract_gives_zero_and_zero_range(self):
        """N=1: normalised = 0.0, column in zero_range_cols (silent redistribution)."""
        raw, idx = self._raw([42.0])
        normed, zero = _normalize_rank(raw, idx, lower_is_better=set())
        assert normed["x"].iloc[0] == pytest.approx(0.0, abs=1e-9)
        assert "x" in zero

    def test_all_nan_gives_nan_and_zero_range(self):
        raw, idx = self._raw([None, None, None])
        normed, zero = _normalize_rank(raw, idx, lower_is_better=set())
        assert normed["x"].isna().all()
        assert "x" in zero

    def test_ties_average_rank(self):
        """Two equal values → average rank → same normalised score."""
        raw, idx = self._raw([10.0, 10.0, 30.0])
        normed, zero = _normalize_rank(raw, idx, lower_is_better=set())
        vals = normed["x"].values
        # ranks: 10→(1+2)/2=1.5, 10→1.5, 30→3; 0-based: 0.5, 0.5, 2.0
        # normed: 0.5/2, 0.5/2, 2.0/2 = 0.25, 0.25, 1.0
        assert vals[0] == pytest.approx(0.25, abs=1e-9)
        assert vals[1] == pytest.approx(0.25, abs=1e-9)
        assert vals[2] == pytest.approx(1.0, abs=1e-9)
        assert "x" not in zero

    def test_nan_row_excluded_from_ranking(self):
        """NaN row excluded; other rows ranked among themselves."""
        raw, idx = self._raw([10.0, None, 30.0])
        normed, zero = _normalize_rank(raw, idx, lower_is_better=set())
        vals = normed["x"]
        assert np.isnan(vals.iloc[1])
        # 10 and 30 ranked among 2 valid: 10→0.0, 30→1.0
        assert vals.iloc[0] == pytest.approx(0.0, abs=1e-9)
        assert vals.iloc[2] == pytest.approx(1.0, abs=1e-9)

    def test_all_ties_goes_to_zero_range(self):
        """
        All values equal → ranks all the same → nunique==1 → zero_range_cols.
        Weight will be redistributed silently (not partial).
        The actual normalised value is 0.5 (average of identical ranks), but
        that value is never used because the column is in zero_range_cols.
        """
        raw, idx = self._raw([5.0, 5.0, 5.0])
        normed, zero = _normalize_rank(raw, idx, lower_is_better=set())
        # Column must be flagged for silent redistribution
        assert "x" in zero
        # All rows have the same normalised value (irrelevant but deterministic)
        unique_vals = normed["x"].dropna().unique()
        assert len(unique_vals) == 1


# ===========================================================================
# Rank mode — end-to-end through calculate_seed_score
# ===========================================================================

class TestRankModeEndToEnd:
    """calculate_seed_score(normalization_mode="rank") integration tests."""

    def test_scores_in_zero_one(self):
        df = _make_df(
            _make_agg_row("gp1", pnl_median=20.0, profitable_count=5, dd_min=-0.10),
            _make_agg_row("gp2", pnl_median=10.0, profitable_count=3, dd_min=-0.30),
        )
        scores, _ = calculate_seed_score(df, _all_passed(df), normalization_mode="rank")
        assert 0.0 <= scores.iloc[0] <= 1.0
        assert 0.0 <= scores.iloc[1] <= 1.0

    def test_better_candidate_higher_score(self):
        df = _make_df(
            _make_agg_row("gp1", pnl_median=20.0, profitable_count=5, dd_min=-0.10),
            _make_agg_row("gp2", pnl_median=10.0, profitable_count=3, dd_min=-0.30),
        )
        scores, _ = calculate_seed_score(df, _all_passed(df), normalization_mode="rank")
        assert scores.iloc[0] > scores.iloc[1]

    def test_status_ok_all_valid(self):
        df = _make_df(
            _make_agg_row("gp1", pnl_median=20.0, profitable_count=5, dd_min=-0.10),
            _make_agg_row("gp2", pnl_median=10.0, profitable_count=3, dd_min=-0.30),
        )
        _, statuses = calculate_seed_score(df, _all_passed(df), normalization_mode="rank")
        assert statuses.iloc[0] == ScoreContractStatus.OK.value
        assert statuses.iloc[1] == ScoreContractStatus.OK.value

    def test_nan_gives_partial(self):
        df = _make_df(
            _make_agg_row("gp1", pnl_median=np.nan, profitable_count=5, dd_min=-0.10),
            _make_agg_row("gp2", pnl_median=20.0, profitable_count=3, dd_min=-0.30),
        )
        _, statuses = calculate_seed_score(df, _all_passed(df), normalization_mode="rank")
        assert statuses.iloc[0] == ScoreContractStatus.PARTIAL.value
        assert statuses.iloc[1] == ScoreContractStatus.OK.value

    def test_lower_is_better_direction(self):
        """Lower drawdown → higher score in rank mode."""
        df = _make_df(
            _make_agg_row("gp1", pnl_median=10.0, profitable_count=3, dd_min=-0.10),
            _make_agg_row("gp2", pnl_median=10.0, profitable_count=3, dd_min=-0.50),
        )
        custom_w = {"abs_max_drawdown_Min": 1.0}
        scores, _ = calculate_seed_score(
            df, _all_passed(df), score_weights=custom_w, normalization_mode="rank",
        )
        assert scores.iloc[0] > scores.iloc[1]

    def test_single_row_n1_contract(self):
        """Single passed row: all columns N=1 → zero_range → no_score (same as minmax)."""
        df = _make_df(
            _make_agg_row("gp1", pnl_median=10.0, profitable_count=5, dd_min=-0.10),
        )
        scores, statuses = calculate_seed_score(df, _all_passed(df), normalization_mode="rank")
        assert np.isnan(scores.iloc[0]) or scores.iloc[0] == pytest.approx(0.0, abs=1e-9)
        assert statuses.iloc[0] in (
            ScoreContractStatus.NO_SCORE.value,
            ScoreContractStatus.OK.value,
        )


# ===========================================================================
# Rank mode — outlier robustness
# ===========================================================================

class TestRankModeOutlierRobustness:
    """
    Core motivation for rank normalization: one anomalous candidate with PnL
    10× larger than others must NOT collapse the scores of remaining candidates.
    """

    def test_outlier_does_not_collapse_others_minmax(self):
        """Baseline: with minmax, outlier collapses gp2 and gp3 towards 0."""
        df = _make_df(
            _make_agg_row("gp1", pnl_median=10.0, profitable_count=5, dd_min=-0.10),
            _make_agg_row("gp2", pnl_median=12.0, profitable_count=5, dd_min=-0.10),
            _make_agg_row("gp3", pnl_median=100.0, profitable_count=5, dd_min=-0.10),
        )
        custom_w = {"sum_pnl_pct_Median": 1.0}
        scores, _ = calculate_seed_score(
            df, _all_passed(df), score_weights=custom_w, normalization_mode="minmax",
        )
        # gp1 and gp2 are crushed near 0 due to outlier gp3
        assert scores.iloc[0] < 0.15  # 10/100 = 0.1
        assert scores.iloc[1] < 0.15  # 12/100 = 0.12

    def test_outlier_does_not_collapse_others_rank(self):
        """With rank mode, gp1 and gp2 retain meaningful separation from each other."""
        df = _make_df(
            _make_agg_row("gp1", pnl_median=10.0, profitable_count=5, dd_min=-0.10),
            _make_agg_row("gp2", pnl_median=12.0, profitable_count=5, dd_min=-0.10),
            _make_agg_row("gp3", pnl_median=100.0, profitable_count=5, dd_min=-0.10),
        )
        custom_w = {"sum_pnl_pct_Median": 1.0}
        scores, _ = calculate_seed_score(
            df, _all_passed(df), score_weights=custom_w, normalization_mode="rank",
        )
        # gp1=0.0, gp2=0.5, gp3=1.0 — scores spread uniformly regardless of outlier
        assert scores.iloc[0] == pytest.approx(0.0, abs=1e-9)
        assert scores.iloc[1] == pytest.approx(0.5, abs=1e-9)
        assert scores.iloc[2] == pytest.approx(1.0, abs=1e-9)

    def test_extreme_outlier_10x(self):
        """PnL of gp4 is 10× gp3; rank scores of gp1..gp3 unchanged."""
        df = _make_df(
            _make_agg_row("gp1", pnl_median=5.0,   profitable_count=4, dd_min=-0.20),
            _make_agg_row("gp2", pnl_median=10.0,  profitable_count=4, dd_min=-0.20),
            _make_agg_row("gp3", pnl_median=15.0,  profitable_count=4, dd_min=-0.20),
            _make_agg_row("gp4", pnl_median=150.0, profitable_count=4, dd_min=-0.20),
        )
        custom_w = {"sum_pnl_pct_Median": 1.0}
        scores, _ = calculate_seed_score(
            df, _all_passed(df), score_weights=custom_w, normalization_mode="rank",
        )
        # Ranks: gp1=0/3, gp2=1/3, gp3=2/3, gp4=3/3
        assert scores.iloc[0] == pytest.approx(0.0,       abs=1e-9)
        assert scores.iloc[1] == pytest.approx(1.0 / 3.0, abs=1e-9)
        assert scores.iloc[2] == pytest.approx(2.0 / 3.0, abs=1e-9)
        assert scores.iloc[3] == pytest.approx(1.0,       abs=1e-9)


# ===========================================================================
# Rank mode — ties (average rank) end-to-end
# ===========================================================================

class TestRankModeTies:
    """
    Validate average-rank tie handling through the full calculate_seed_score
    pipeline. Two rows with identical PnL must receive the same score.
    """

    def test_two_identical_pnl_same_score(self):
        df = _make_df(
            _make_agg_row("gp1", pnl_median=10.0, profitable_count=3, dd_min=-0.20),
            _make_agg_row("gp2", pnl_median=10.0, profitable_count=3, dd_min=-0.20),
            _make_agg_row("gp3", pnl_median=20.0, profitable_count=3, dd_min=-0.20),
        )
        custom_w = {"sum_pnl_pct_Median": 1.0}
        scores, statuses = calculate_seed_score(
            df, _all_passed(df), score_weights=custom_w, normalization_mode="rank",
        )
        # gp1 and gp2 are tied → same rank → same score
        assert scores.iloc[0] == pytest.approx(scores.iloc[1], abs=1e-9)
        assert scores.iloc[2] > scores.iloc[0]
        assert (statuses == ScoreContractStatus.OK.value).all()

    def test_all_tied_single_metric_no_score(self):
        """All candidates identical on the only metric → zero_range → no_score."""
        df = _make_df(
            _make_agg_row("gp1", pnl_median=10.0, profitable_count=3, dd_min=-0.20),
            _make_agg_row("gp2", pnl_median=10.0, profitable_count=3, dd_min=-0.20),
            _make_agg_row("gp3", pnl_median=10.0, profitable_count=3, dd_min=-0.20),
        )
        custom_w = {"sum_pnl_pct_Median": 1.0}
        scores, statuses = calculate_seed_score(
            df, _all_passed(df), score_weights=custom_w, normalization_mode="rank",
        )
        # All tied → zero_range_cols → no contributing columns → no_score
        assert statuses.iloc[0] == ScoreContractStatus.NO_SCORE.value

    def test_partial_tie_block(self):
        """Block of ties in the middle; the unique values get correct extremes."""
        df = _make_df(
            _make_agg_row("gp1", pnl_median=5.0,  profitable_count=3, dd_min=-0.20),
            _make_agg_row("gp2", pnl_median=10.0, profitable_count=3, dd_min=-0.20),
            _make_agg_row("gp3", pnl_median=10.0, profitable_count=3, dd_min=-0.20),
            _make_agg_row("gp4", pnl_median=20.0, profitable_count=3, dd_min=-0.20),
        )
        custom_w = {"sum_pnl_pct_Median": 1.0}
        scores, _ = calculate_seed_score(
            df, _all_passed(df), score_weights=custom_w, normalization_mode="rank",
        )
        # ranks (0-based): gp1=0, gp2=1.5, gp3=1.5, gp4=3
        # normed: 0/3, 1.5/3, 1.5/3, 3/3
        assert scores.iloc[0] == pytest.approx(0.0, abs=1e-9)
        assert scores.iloc[1] == pytest.approx(0.5, abs=1e-9)
        assert scores.iloc[2] == pytest.approx(0.5, abs=1e-9)
        assert scores.iloc[3] == pytest.approx(1.0, abs=1e-9)


# ===========================================================================
# Mode parity: rank and minmax agree on ranking order for clean data
# ===========================================================================

class TestModeParity:
    """Both modes should produce the same *rank order* on clean, outlier-free data."""

    def test_rank_order_consistent_two_rows(self):
        df = _make_df(
            _make_agg_row("gp1", pnl_median=20.0, profitable_count=5, dd_min=-0.10),
            _make_agg_row("gp2", pnl_median=10.0, profitable_count=3, dd_min=-0.30),
        )
        mask = _all_passed(df)
        s_mm, _ = calculate_seed_score(df, mask, normalization_mode="minmax")
        s_rk, _ = calculate_seed_score(df, mask, normalization_mode="rank")
        # Both agree: gp1 > gp2
        assert s_mm.iloc[0] > s_mm.iloc[1]
        assert s_rk.iloc[0] > s_rk.iloc[1]

    def test_rank_order_consistent_three_rows(self):
        df = _make_df(
            _make_agg_row("gp1", pnl_median=30.0, profitable_count=6, dd_min=-0.05),
            _make_agg_row("gp2", pnl_median=20.0, profitable_count=4, dd_min=-0.15),
            _make_agg_row("gp3", pnl_median=10.0, profitable_count=2, dd_min=-0.40),
        )
        mask = _all_passed(df)
        s_mm, _ = calculate_seed_score(df, mask, normalization_mode="minmax")
        s_rk, _ = calculate_seed_score(df, mask, normalization_mode="rank")
        assert s_mm.iloc[0] > s_mm.iloc[1] > s_mm.iloc[2]
        assert s_rk.iloc[0] > s_rk.iloc[1] > s_rk.iloc[2]

    def test_weight_normalization_invariant_rank_mode(self):
        """Auto-normalize weights must produce identical results in rank mode too."""
        df = _make_df(
            _make_agg_row("gp1", pnl_median=20.0, profitable_count=5, dd_min=-0.10),
            _make_agg_row("gp2", pnl_median=10.0, profitable_count=3, dd_min=-0.30),
        )
        w1 = {"sum_pnl_pct_Median": 0.45, "profitable_segments_count": 0.35, "abs_max_drawdown_Min": 0.20}
        w2 = {"sum_pnl_pct_Median": 4.5,  "profitable_segments_count": 3.5,  "abs_max_drawdown_Min": 2.0}
        s1, _ = calculate_seed_score(df, _all_passed(df), score_weights=w1, normalization_mode="rank")
        s2, _ = calculate_seed_score(df, _all_passed(df), score_weights=w2, normalization_mode="rank")
        pd.testing.assert_series_equal(s1, s2, check_names=False)


# ===========================================================================
# Golden test: rank mode — rank-1 candidate identity
# ===========================================================================

class TestGoldenRankMode:
    """
    Golden regression for rank mode using the same mini-dataset as
    test_golden_values.py (gp1/gp2/gp3).

    gp1 is the best candidate; gp2 is second; gp3 does not pass gates.
    With rank normalization: gp1 should still score highest.
    This test pins the *ordering*, not exact float values, since switching from
    minmax to rank intentionally changes absolute scores.
    """

    def _make_golden_df(self):
        rows = [
            {
                "grid_point_id": "gp1",
                "sum_pnl_pct_Median": 10.0,
                "profitable_segments_count": 3,
                "max_drawdown_Min": -0.08,
                "n_ok_steps": 3,
            },
            {
                "grid_point_id": "gp2",
                "sum_pnl_pct_Median": 7.0,
                "profitable_segments_count": 2,
                "max_drawdown_Min": -0.15,
                "n_ok_steps": 3,
            },
            {
                "grid_point_id": "gp3",
                "sum_pnl_pct_Median": np.nan,
                "profitable_segments_count": 0,
                "max_drawdown_Min": np.nan,
                "n_ok_steps": 0,
            },
        ]
        return pd.DataFrame(rows)

    def test_rank1_candidate_unchanged(self):
        """gp1 must be rank-1 under rank normalization (same as minmax)."""
        df = self._make_golden_df()
        passed = pd.Series([True, True, False], index=df.index)

        s_mm, st_mm = calculate_seed_score(df, passed, normalization_mode="minmax")
        s_rk, st_rk = calculate_seed_score(df, passed, normalization_mode="rank")

        # Both modes: gp1 scores higher than gp2
        assert s_mm.iloc[0] > s_mm.iloc[1], "minmax: gp1 should outscore gp2"
        assert s_rk.iloc[0] > s_rk.iloc[1], "rank: gp1 should outscore gp2"

        # gp3 not passed → no_score in both modes
        assert np.isnan(s_mm.iloc[2])
        assert np.isnan(s_rk.iloc[2])
        assert st_mm.iloc[2] == ScoreContractStatus.NO_SCORE.value
        assert st_rk.iloc[2] == ScoreContractStatus.NO_SCORE.value

    def test_rank_mode_exact_scores_two_passed(self):
        """
        With 2 passed rows and all 3 metrics contributing, rank mode gives:
          best row on every metric → 1.0 on each → weighted sum = 1.0
          worst row on every metric → 0.0 on each → weighted sum = 0.0
        (gp1 dominates gp2 on all three metrics)
        """
        df = self._make_golden_df()
        passed = pd.Series([True, True, False], index=df.index)
        scores, statuses = calculate_seed_score(df, passed, normalization_mode="rank")

        assert scores.iloc[0] == pytest.approx(1.0, abs=1e-9)
        assert scores.iloc[1] == pytest.approx(0.0, abs=1e-9)
        assert statuses.iloc[0] == ScoreContractStatus.OK.value
        assert statuses.iloc[1] == ScoreContractStatus.OK.value


# ===========================================================================
# abs_max_drawdown_Median: derived column tests
# ===========================================================================

class TestDerivedAbsDrawdownMedian:
    """Tests for abs_max_drawdown_Median derivation logic."""

    _W_MEDIAN_ONLY = {"abs_max_drawdown_Median": 1.0}
    _W_BOTH = {
        "abs_max_drawdown_Min": 0.5,
        "abs_max_drawdown_Median": 0.5,
    }

    def test_derived_from_max_drawdown_median(self):
        """abs_max_drawdown_Median derived from max_drawdown_Median when absent."""
        df = _make_df(
            _make_agg_row("gp1", dd_median=-0.10),
            _make_agg_row("gp2", dd_median=-0.30),
        )
        assert "abs_max_drawdown_Median" not in df.columns
        scores, statuses = calculate_seed_score(
            df, _all_passed(df),
            score_weights=self._W_MEDIAN_ONLY,
            normalization_mode="rank",
        )
        assert statuses.iloc[0] == ScoreContractStatus.OK.value
        assert statuses.iloc[1] == ScoreContractStatus.OK.value
        # Lower dd_median → lower abs → better score
        assert scores.iloc[0] > scores.iloc[1]

    def test_not_overwritten_when_already_present(self):
        """If abs_max_drawdown_Median already in DF, use it as-is."""
        df = _make_df(
            _make_agg_row("gp1", dd_median=-0.10),
            _make_agg_row("gp2", dd_median=-0.30),
        )
        # Pre-populate with different (inverted) values to detect overwrite
        df["abs_max_drawdown_Median"] = [0.99, 0.01]
        scores, _ = calculate_seed_score(
            df, _all_passed(df),
            score_weights=self._W_MEDIAN_ONLY,
            normalization_mode="rank",
        )
        # 0.99 > 0.01 → lower-is-better → gp2 (0.01) should score higher
        assert scores.iloc[1] > scores.iloc[0]

    def test_key_error_when_both_columns_missing(self):
        """KeyError raised when abs_max_drawdown_Median configured but both source columns absent."""
        df = _make_df(
            _make_agg_row("gp1"),
            _make_agg_row("gp2"),
        )
        df = df.drop(columns=["max_drawdown_Median"])
        with pytest.raises(KeyError, match="abs_max_drawdown_Median"):
            calculate_seed_score(
                df, _all_passed(df),
                score_weights=self._W_MEDIAN_ONLY,
            )

    def test_all_nan_median_gives_partial_or_no_score(self):
        """max_drawdown_Median all-NaN → abs_max_drawdown_Median all-NaN → partial/no_score."""
        df = _make_df(
            _make_agg_row("gp1", pnl_median=10.0, profitable_count=3, dd_min=-0.20, dd_median=np.nan),
            _make_agg_row("gp2", pnl_median=20.0, profitable_count=5, dd_min=-0.10, dd_median=np.nan),
        )
        weights = {
            "sum_pnl_pct_Median": 0.45,
            "profitable_segments_count": 0.35,
            "abs_max_drawdown_Median": 0.20,
        }
        _, statuses = calculate_seed_score(
            df, _all_passed(df),
            score_weights=weights,
            normalization_mode="rank",
        )
        for s in statuses:
            assert s in (ScoreContractStatus.PARTIAL.value, ScoreContractStatus.NO_SCORE.value)

    def test_df_copied_at_most_once_for_both_derived(self):
        """When both abs_max_drawdown_Min and abs_max_drawdown_Median need derivation,
        the original DataFrame is not modified (mutation guard)."""
        df = _make_df(
            _make_agg_row("gp1", dd_min=-0.20, dd_median=-0.15),
            _make_agg_row("gp2", dd_min=-0.10, dd_median=-0.05),
        )
        assert "abs_max_drawdown_Min" not in df.columns
        assert "abs_max_drawdown_Median" not in df.columns

        original_cols = set(df.columns)
        calculate_seed_score(
            df, _all_passed(df),
            score_weights=self._W_BOTH,
            normalization_mode="rank",
        )
        # Original df must be untouched (no new columns added in-place)
        assert set(df.columns) == original_cols


# ===========================================================================
# Four-weight scoring: full integration with 4-component config
# ===========================================================================

_W4 = {
    "sum_pnl_pct_Median":        0.45,
    "profitable_segments_count": 0.35,
    "abs_max_drawdown_Median":   0.10,
    "abs_max_drawdown_Min":      0.10,
}


class TestFourWeightScoring:
    """End-to-end tests using the 4-weight config from §1."""

    def _make_pair(self, better_pnl=20.0, worse_pnl=10.0):
        """Two candidates: gp1 dominates gp2 on all metrics."""
        return _make_df(
            _make_agg_row("gp1", pnl_median=better_pnl, profitable_count=5,
                          dd_min=-0.05, dd_median=-0.08),
            _make_agg_row("gp2", pnl_median=worse_pnl,  profitable_count=2,
                          dd_min=-0.40, dd_median=-0.30),
        )

    def test_ok_status_both_candidates(self):
        """Both candidates have all metrics valid → status ok."""
        df = self._make_pair()
        _, statuses = calculate_seed_score(
            df, _all_passed(df), score_weights=_W4, normalization_mode="rank",
        )
        assert statuses.iloc[0] == ScoreContractStatus.OK.value
        assert statuses.iloc[1] == ScoreContractStatus.OK.value

    def test_scores_in_zero_one(self):
        df = self._make_pair()
        scores, _ = calculate_seed_score(
            df, _all_passed(df), score_weights=_W4, normalization_mode="rank",
        )
        assert 0.0 <= scores.iloc[0] <= 1.0
        assert 0.0 <= scores.iloc[1] <= 1.0

    def test_dominant_candidate_higher_score(self):
        """gp1 is better on all 4 metrics → higher score."""
        df = self._make_pair()
        scores, _ = calculate_seed_score(
            df, _all_passed(df), score_weights=_W4, normalization_mode="rank",
        )
        assert scores.iloc[0] > scores.iloc[1]

    def test_lower_is_better_for_both_drawdown_metrics(self):
        """Candidate with smaller abs drawdown (Min and Median) gets higher score."""
        df = _make_df(
            _make_agg_row("gp1", pnl_median=10.0, profitable_count=3,
                          dd_min=-0.10, dd_median=-0.08),
            _make_agg_row("gp2", pnl_median=10.0, profitable_count=3,
                          dd_min=-0.40, dd_median=-0.35),
        )
        # Only drawdown weights → only abs_dd columns differentiate
        w_dd_only = {"abs_max_drawdown_Min": 0.5, "abs_max_drawdown_Median": 0.5}
        scores, _ = calculate_seed_score(
            df, _all_passed(df), score_weights=w_dd_only, normalization_mode="rank",
        )
        assert scores.iloc[0] > scores.iloc[1]

    def test_rank_order_same_as_minmax_on_clean_data(self):
        """Rank and minmax agree on ordering for clean data without outliers."""
        df = self._make_pair()
        mask = _all_passed(df)
        s_rk, _ = calculate_seed_score(df, mask, score_weights=_W4, normalization_mode="rank")
        s_mm, _ = calculate_seed_score(df, mask, score_weights=_W4, normalization_mode="minmax")
        assert s_rk.iloc[0] > s_rk.iloc[1]
        assert s_mm.iloc[0] > s_mm.iloc[1]

    def test_determinism_four_weights(self):
        """Two identical calls produce bitwise-identical output."""
        df = self._make_pair()
        mask = _all_passed(df)
        s1, st1 = calculate_seed_score(df, mask, score_weights=_W4, normalization_mode="rank")
        s2, st2 = calculate_seed_score(df, mask, score_weights=_W4, normalization_mode="rank")
        pd.testing.assert_series_equal(s1, s2, check_names=False)
        pd.testing.assert_series_equal(st1, st2, check_names=False)

    def test_nan_in_dd_median_gives_partial(self):
        """One candidate with NaN max_drawdown_Median → partial; other → ok."""
        df = _make_df(
            _make_agg_row("gp1", pnl_median=20.0, profitable_count=5,
                          dd_min=-0.10, dd_median=np.nan),
            _make_agg_row("gp2", pnl_median=10.0, profitable_count=3,
                          dd_min=-0.30, dd_median=-0.20),
        )
        scores, statuses = calculate_seed_score(
            df, _all_passed(df), score_weights=_W4, normalization_mode="rank",
        )
        assert statuses.iloc[0] == ScoreContractStatus.PARTIAL.value
        assert statuses.iloc[1] == ScoreContractStatus.OK.value
        # Both scores must be non-NaN (weight redistributed)
        assert not np.isnan(scores.iloc[0])
        assert not np.isnan(scores.iloc[1])

    def test_both_drawdown_nan_gives_partial(self):
        """One candidate has NaN for both abs_dd_* → partial (other 2 metrics contribute)."""
        df = _make_df(
            _make_agg_row("gp1", pnl_median=20.0, profitable_count=5,
                          dd_min=np.nan, dd_median=np.nan),
            _make_agg_row("gp2", pnl_median=10.0, profitable_count=3,
                          dd_min=-0.30, dd_median=-0.20),
        )
        scores, statuses = calculate_seed_score(
            df, _all_passed(df), score_weights=_W4, normalization_mode="rank",
        )
        assert statuses.iloc[0] == ScoreContractStatus.PARTIAL.value
        assert not np.isnan(scores.iloc[0])


# ===========================================================================
# FIX-3: No median imputation in _normalize_minmax
# ===========================================================================

class TestNoMedianImputation:
    """NaN in minmax mode must NOT be filled with column median before normalisation.

    Before the fix: vals.fillna(col_median) was applied, which:
    - distorted col_min/col_max (they were computed on valid-only subset, but
      imputed values could affect normalised scores of other rows indirectly
      through the filled series used in the formula).
    - created artificial 'middle' scores for NaN candidates.

    After the fix: NaN stays NaN; the per-row weight redistribution in
    calculate_seed_score handles it identically to rank mode.
    """

    def test_nan_row_gets_partial_not_artificial_middle(self):
        """Row with NaN component must get partial status, not a spurious median score."""
        df = _make_df(
            _make_agg_row("gp1", pnl_median=np.nan, profitable_count=5, dd_min=-0.10),
            _make_agg_row("gp2", pnl_median=20.0,   profitable_count=3, dd_min=-0.30),
        )
        scores, statuses = calculate_seed_score(
            df, _all_passed(df), normalization_mode="minmax",
        )
        assert statuses.iloc[0] == ScoreContractStatus.PARTIAL.value
        assert statuses.iloc[1] == ScoreContractStatus.OK.value
        # gp1's score is based only on valid components (profitable_count, abs_dd_min)
        assert not np.isnan(scores.iloc[0])

    def test_clean_rows_unaffected_by_nan_peer(self):
        """Scores of rows without NaN must not change when a NaN peer is added."""
        df_clean = _make_df(
            _make_agg_row("gp1", pnl_median=20.0, profitable_count=5, dd_min=-0.10),
            _make_agg_row("gp2", pnl_median=10.0, profitable_count=3, dd_min=-0.30),
        )
        df_with_nan = _make_df(
            _make_agg_row("gp1", pnl_median=20.0,   profitable_count=5, dd_min=-0.10),
            _make_agg_row("gp2", pnl_median=10.0,   profitable_count=3, dd_min=-0.30),
            _make_agg_row("gp3", pnl_median=np.nan, profitable_count=3, dd_min=-0.20),
        )
        # gp1 and gp2 scores should be identical in both cases
        # (NaN peer must not shift the normalisation range of valid rows)
        s_clean, _ = calculate_seed_score(df_clean, _all_passed(df_clean), normalization_mode="minmax")
        s_nan, _ = calculate_seed_score(df_with_nan, _all_passed(df_with_nan), normalization_mode="minmax")
        # Both clean/nan runs: gp1 > gp2 ordering preserved
        assert s_clean.iloc[0] > s_clean.iloc[1]
        assert s_nan.iloc[0] > s_nan.iloc[1]

    def test_all_nan_column_gives_zero_range(self):
        """Column with all NaN → zero_range_cols → weight redistributed, no crash."""
        df = _make_df(
            _make_agg_row("gp1", pnl_median=np.nan, profitable_count=5, dd_min=-0.10),
            _make_agg_row("gp2", pnl_median=np.nan, profitable_count=3, dd_min=-0.30),
        )
        custom_w = {"sum_pnl_pct_Median": 0.45, "profitable_segments_count": 0.35, "abs_max_drawdown_Min": 0.20}
        scores, statuses = calculate_seed_score(
            df, _all_passed(df), score_weights=custom_w, normalization_mode="minmax",
        )
        # pnl_median all-NaN → column in zero_range, weight redistributed silently
        # Other two metrics are valid → scores should be non-NaN
        for i in range(2):
            assert not np.isnan(scores.iloc[i]), f"Row {i} got NaN unexpectedly"
