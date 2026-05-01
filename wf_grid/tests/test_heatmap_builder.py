"""
Unit tests for Этап 5: wf_grid/bucket/heatmap_builder.py
"""

from __future__ import annotations

import pandas as pd
import pytest

from wf_grid.bucket.heatmap_builder import build_risk_heatmap, build_stability_heatmap

_EN_DASH = "\u2013"


def _make_bucket_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


# ===========================================================================
# Basic functionality
# ===========================================================================

class TestHeatmapBasic:
    def test_pivot_shape(self):
        """Pivot shape = len(unique atr_buckets) × len(unique mult_ticks)."""
        df = _make_bucket_df([
            {"atr_bucket": 10, "mult_bucket_ticks": 10, "bucket_stability_score": 0.8},
            {"atr_bucket": 10, "mult_bucket_ticks": 11, "bucket_stability_score": 0.6},
            {"atr_bucket": 12, "mult_bucket_ticks": 10, "bucket_stability_score": 0.7},
            {"atr_bucket": 12, "mult_bucket_ticks": 11, "bucket_stability_score": 0.5},
        ])
        result = build_stability_heatmap(df, atr_bucket_step=2, mult_bucket_step=0.2)
        assert result.shape == (2, 2)

    def test_values_correct(self):
        """Values in pivot match bucket_stability_score."""
        df = _make_bucket_df([
            {"atr_bucket": 10, "mult_bucket_ticks": 10, "bucket_stability_score": 0.8},
            {"atr_bucket": 10, "mult_bucket_ticks": 11, "bucket_stability_score": 0.6},
            {"atr_bucket": 12, "mult_bucket_ticks": 10, "bucket_stability_score": 0.7},
            {"atr_bucket": 12, "mult_bucket_ticks": 11, "bucket_stability_score": 0.5},
        ])
        result = build_stability_heatmap(df, atr_bucket_step=2, mult_bucket_step=0.2)
        # Find the cell for atr_bucket=10, mult_ticks=10
        atr_label = f"10{_EN_DASH}12"
        mult_label = f"2.0{_EN_DASH}2.2"
        assert result.loc[atr_label, mult_label] == pytest.approx(0.8)

    def test_sorted_atr_ascending(self):
        """Rows are sorted by atr_bucket ASC."""
        df = _make_bucket_df([
            {"atr_bucket": 12, "mult_bucket_ticks": 10, "bucket_stability_score": 0.7},
            {"atr_bucket": 10, "mult_bucket_ticks": 10, "bucket_stability_score": 0.8},
        ])
        result = build_stability_heatmap(df, atr_bucket_step=2, mult_bucket_step=0.2)
        assert list(result.index)[0].startswith("10")
        assert list(result.index)[1].startswith("12")

    def test_sorted_mult_ascending(self):
        """Columns are sorted by mult_bucket_ticks ASC."""
        df = _make_bucket_df([
            {"atr_bucket": 10, "mult_bucket_ticks": 15, "bucket_stability_score": 0.6},
            {"atr_bucket": 10, "mult_bucket_ticks": 10, "bucket_stability_score": 0.8},
        ])
        result = build_stability_heatmap(df, atr_bucket_step=2, mult_bucket_step=0.2)
        cols = list(result.columns)
        # 10 * 0.2 = 2.0, 15 * 0.2 = 3.0
        assert cols[0].startswith("2.0")
        assert cols[1].startswith("3.0")


# ===========================================================================
# Labels
# ===========================================================================

class TestHeatmapLabels:
    def test_atr_index_en_dash(self):
        df = _make_bucket_df([
            {"atr_bucket": 10, "mult_bucket_ticks": 10, "bucket_stability_score": 0.5},
        ])
        result = build_stability_heatmap(df, atr_bucket_step=2, mult_bucket_step=0.2)
        assert _EN_DASH in result.index[0]
        assert "-" not in result.index[0]

    def test_mult_columns_en_dash(self):
        df = _make_bucket_df([
            {"atr_bucket": 10, "mult_bucket_ticks": 10, "bucket_stability_score": 0.5},
        ])
        result = build_stability_heatmap(df, atr_bucket_step=2, mult_bucket_step=0.2)
        assert _EN_DASH in result.columns[0]

    def test_atr_label_format(self):
        """ATR label: '{ab}–{ab+step}'."""
        df = _make_bucket_df([
            {"atr_bucket": 10, "mult_bucket_ticks": 10, "bucket_stability_score": 0.5},
        ])
        result = build_stability_heatmap(df, atr_bucket_step=2, mult_bucket_step=0.2)
        assert result.index[0] == f"10{_EN_DASH}12"

    def test_mult_label_format_one_decimal(self):
        """Mult label: '{mt*step:.1f}–{(mt+1)*step:.1f}'."""
        df = _make_bucket_df([
            {"atr_bucket": 10, "mult_bucket_ticks": 10, "bucket_stability_score": 0.5},
        ])
        result = build_stability_heatmap(df, atr_bucket_step=2, mult_bucket_step=0.2)
        assert result.columns[0] == f"2.0{_EN_DASH}2.2"

    def test_index_name(self):
        df = _make_bucket_df([
            {"atr_bucket": 10, "mult_bucket_ticks": 10, "bucket_stability_score": 0.5},
        ])
        result = build_stability_heatmap(df, atr_bucket_step=2, mult_bucket_step=0.2)
        assert result.index.name == "ATR \\ MULT"

    def test_columns_name_is_none(self):
        df = _make_bucket_df([
            {"atr_bucket": 10, "mult_bucket_ticks": 10, "bucket_stability_score": 0.5},
        ])
        result = build_stability_heatmap(df, atr_bucket_step=2, mult_bucket_step=0.2)
        assert result.columns.name is None


# ===========================================================================
# NaN handling
# ===========================================================================

class TestHeatmapNanHandling:
    def test_nan_preserved(self):
        """Bucket missing from input → NaN in pivot (no fill)."""
        df = _make_bucket_df([
            {"atr_bucket": 10, "mult_bucket_ticks": 10, "bucket_stability_score": 0.8},
            {"atr_bucket": 12, "mult_bucket_ticks": 11, "bucket_stability_score": 0.5},
            # (10, 11) and (12, 10) are missing
        ])
        result = build_stability_heatmap(df, atr_bucket_step=2, mult_bucket_step=0.2)
        atr_label_10 = f"10{_EN_DASH}12"
        mult_label_11 = f"2.2{_EN_DASH}2.4"
        assert pd.isna(result.loc[atr_label_10, mult_label_11])

    def test_nan_not_dropped(self):
        """fillna/dropna must NOT be applied."""
        df = _make_bucket_df([
            {"atr_bucket": 10, "mult_bucket_ticks": 10, "bucket_stability_score": 0.8},
            {"atr_bucket": 12, "mult_bucket_ticks": 11, "bucket_stability_score": 0.5},
        ])
        result = build_stability_heatmap(df, atr_bucket_step=2, mult_bucket_step=0.2)
        # Result should have NaN cells, not be trimmed
        assert result.shape == (2, 2)
        assert result.isna().any().any()


# ===========================================================================
# Edge cases
# ===========================================================================

class TestHeatmapEdgeCases:
    def test_empty_df_returns_empty(self):
        result = build_stability_heatmap(
            pd.DataFrame(), atr_bucket_step=2, mult_bucket_step=0.2
        )
        assert result.empty

    def test_none_input_returns_empty(self):
        result = build_stability_heatmap(
            None, atr_bucket_step=2, mult_bucket_step=0.2
        )
        assert result.empty

    def test_missing_required_columns_returns_empty(self):
        df = pd.DataFrame({"atr_bucket": [10], "bucket_stability_score": [0.5]})
        result = build_stability_heatmap(df, atr_bucket_step=2, mult_bucket_step=0.2)
        assert result.empty

    def test_single_bucket(self):
        df = _make_bucket_df([
            {"atr_bucket": 10, "mult_bucket_ticks": 10, "bucket_stability_score": 0.75},
        ])
        result = build_stability_heatmap(df, atr_bucket_step=2, mult_bucket_step=0.2)
        assert result.shape == (1, 1)
        assert result.iloc[0, 0] == pytest.approx(0.75)


# ===========================================================================
# Parameters are required (not default kwargs)
# ===========================================================================

class TestHeatmapRequiredParams:
    def test_requires_atr_bucket_step(self):
        df = _make_bucket_df([
            {"atr_bucket": 10, "mult_bucket_ticks": 10, "bucket_stability_score": 0.5},
        ])
        with pytest.raises(TypeError):
            build_stability_heatmap(df, mult_bucket_step=0.2)  # missing atr_bucket_step

    def test_requires_mult_bucket_step(self):
        df = _make_bucket_df([
            {"atr_bucket": 10, "mult_bucket_ticks": 10, "bucket_stability_score": 0.5},
        ])
        with pytest.raises(TypeError):
            build_stability_heatmap(df, atr_bucket_step=2)  # missing mult_bucket_step

    def test_requires_both_params(self):
        df = _make_bucket_df([
            {"atr_bucket": 10, "mult_bucket_ticks": 10, "bucket_stability_score": 0.5},
        ])
        with pytest.raises(TypeError):
            build_stability_heatmap(df)  # missing both


# ===========================================================================
# Risk heatmap (build_risk_heatmap)
# ===========================================================================

def _make_risk_df(rows: list[dict]) -> pd.DataFrame:
    """Helper: create bucket_matrix_df-like df with max_drawdown_Median."""
    return pd.DataFrame(rows)


class TestRiskHeatmapBasic:
    def test_risk_heatmap_shape(self):
        """Размерность pivot совпадает со stability heatmap при тех же бакетах."""
        df = _make_risk_df([
            {"atr_bucket": 10, "mult_bucket_ticks": 10,
             "bucket_stability_score": 0.8, "max_drawdown_Median": -0.10},
            {"atr_bucket": 10, "mult_bucket_ticks": 11,
             "bucket_stability_score": 0.6, "max_drawdown_Median": -0.20},
            {"atr_bucket": 12, "mult_bucket_ticks": 10,
             "bucket_stability_score": 0.7, "max_drawdown_Median": -0.15},
            {"atr_bucket": 12, "mult_bucket_ticks": 11,
             "bucket_stability_score": 0.5, "max_drawdown_Median": -0.25},
        ])
        stability = build_stability_heatmap(df, atr_bucket_step=2, mult_bucket_step=0.2)
        risk = build_risk_heatmap(df, atr_bucket_step=2, mult_bucket_step=0.2)
        assert risk.shape == stability.shape

    def test_risk_heatmap_values_from_max_drawdown_median(self):
        """Значения pivot = max_drawdown_Median бакета."""
        df = _make_risk_df([
            {"atr_bucket": 10, "mult_bucket_ticks": 10,
             "bucket_stability_score": 0.8, "max_drawdown_Median": -0.15},
        ])
        result = build_risk_heatmap(df, atr_bucket_step=2, mult_bucket_step=0.2)
        atr_label = f"10{_EN_DASH}12"
        mult_label = f"2.0{_EN_DASH}2.2"
        assert result.loc[atr_label, mult_label] == pytest.approx(-0.15)

    def test_risk_heatmap_labels_match_stability(self):
        """Подписи осей совпадают со stability heatmap."""
        df = _make_risk_df([
            {"atr_bucket": 10, "mult_bucket_ticks": 10,
             "bucket_stability_score": 0.8, "max_drawdown_Median": -0.10},
            {"atr_bucket": 12, "mult_bucket_ticks": 11,
             "bucket_stability_score": 0.5, "max_drawdown_Median": -0.20},
        ])
        stability = build_stability_heatmap(df, atr_bucket_step=2, mult_bucket_step=0.2)
        risk = build_risk_heatmap(df, atr_bucket_step=2, mult_bucket_step=0.2)
        assert list(risk.index) == list(stability.index)
        assert list(risk.columns) == list(stability.columns)

    def test_risk_heatmap_index_name(self):
        """index.name == 'ATR \\ MULT'."""
        df = _make_risk_df([
            {"atr_bucket": 10, "mult_bucket_ticks": 10,
             "bucket_stability_score": 0.5, "max_drawdown_Median": -0.10},
        ])
        result = build_risk_heatmap(df, atr_bucket_step=2, mult_bucket_step=0.2)
        assert result.index.name == "ATR \\ MULT"

    def test_risk_heatmap_sorted_asc(self):
        """Строки и колонки отсортированы по возрастанию."""
        df = _make_risk_df([
            {"atr_bucket": 12, "mult_bucket_ticks": 11,
             "bucket_stability_score": 0.5, "max_drawdown_Median": -0.20},
            {"atr_bucket": 10, "mult_bucket_ticks": 10,
             "bucket_stability_score": 0.8, "max_drawdown_Median": -0.10},
        ])
        result = build_risk_heatmap(df, atr_bucket_step=2, mult_bucket_step=0.2)
        assert list(result.index)[0].startswith("10")
        assert list(result.index)[1].startswith("12")


class TestRiskHeatmapEdgeCases:
    def test_risk_heatmap_empty_when_no_data(self):
        """Пустой pivot при пустом input."""
        result = build_risk_heatmap(
            pd.DataFrame(), atr_bucket_step=2, mult_bucket_step=0.2
        )
        assert result.empty

    def test_risk_heatmap_empty_on_none(self):
        """None input → empty DataFrame."""
        result = build_risk_heatmap(
            None, atr_bucket_step=2, mult_bucket_step=0.2
        )
        assert result.empty

    def test_risk_heatmap_missing_column_returns_empty(self):
        """Без max_drawdown_Median → empty df."""
        df = _make_risk_df([
            {"atr_bucket": 10, "mult_bucket_ticks": 10,
             "bucket_stability_score": 0.8},
        ])
        result = build_risk_heatmap(df, atr_bucket_step=2, mult_bucket_step=0.2)
        assert result.empty

    def test_risk_heatmap_nan_preserved(self):
        """Missing combinations → NaN in pivot (no fill)."""
        df = _make_risk_df([
            {"atr_bucket": 10, "mult_bucket_ticks": 10,
             "bucket_stability_score": 0.8, "max_drawdown_Median": -0.10},
            {"atr_bucket": 12, "mult_bucket_ticks": 11,
             "bucket_stability_score": 0.5, "max_drawdown_Median": -0.25},
        ])
        result = build_risk_heatmap(df, atr_bucket_step=2, mult_bucket_step=0.2)
        assert result.shape == (2, 2)
        assert result.isna().any().any()
