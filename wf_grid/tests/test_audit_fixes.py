"""
Tests covering all post-audit fix verifications.

Groups:
  2.2  — validate_ohlc_data: strict=True + non-DatetimeIndex raises
  3.3  — _defensive_fallback / _make_error_result: INVALID not 0.0
  4.3  — compute_score_discrimination: configurable min_passed threshold
  5.6  — legacy mode: seed_gate_passed as primary sort key
  5.7  — min_total_trades loaded from YAML
  5.8  — apply_candidate_gates: missing ok_ratio / total_oos_trades raises
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.data.validator import validate_ohlc_data
from supertrend_optimizer.utils.exceptions import DataValidationError
from supertrend_optimizer.utils.constants import INVALID_METRIC_VALUE

from wf_grid.config.loader import load_grid_config
from wf_grid.config.schema import (
    BacktestConfig,
    CandidateGatesConfig,
    DataConfig,
    GatesConfig,
    GridConfig,
    RankingConfig,
    ScoringConfig,
    StepGatesConfig,
)
from wf_grid.gates.gates import apply_candidate_gates
from wf_grid.ranking.ranker import rank_candidates
from wf_grid.ranking.scoring import compute_score_discrimination
from wf_grid.wf.step_executor import _defensive_fallback
from wf_grid.wf.runner import _make_error_result


# ===========================================================================
# 2.2 — validate_ohlc_data: strict=True + non-DatetimeIndex raises
# ===========================================================================

class TestStrictNonDatetimeIndex:
    """strict=True must raise DataValidationError when index is not DatetimeIndex."""

    def _valid_ohlc_df(self, index=None) -> pd.DataFrame:
        data = {
            "open":  [1.0, 2.0, 3.0],
            "high":  [1.5, 2.5, 3.5],
            "low":   [0.9, 1.9, 2.9],
            "close": [1.2, 2.2, 3.2],
        }
        df = pd.DataFrame(data)
        if index is not None:
            df.index = index
        return df

    def test_range_index_strict_raises(self):
        df = self._valid_ohlc_df()  # default RangeIndex
        with pytest.raises(DataValidationError, match="DatetimeIndex"):
            validate_ohlc_data(df, strict=True)

    def test_string_index_strict_raises(self):
        df = self._valid_ohlc_df(index=["a", "b", "c"])
        with pytest.raises(DataValidationError, match="DatetimeIndex"):
            validate_ohlc_data(df, strict=True)

    def test_int_index_strict_raises(self):
        df = self._valid_ohlc_df(index=[10, 20, 30])
        with pytest.raises(DataValidationError, match="DatetimeIndex"):
            validate_ohlc_data(df, strict=True)

    def test_datetime_index_strict_does_not_raise(self):
        idx = pd.date_range("2020-01-01", periods=3, freq="D")
        df = self._valid_ohlc_df(index=idx)
        result = validate_ohlc_data(df, strict=True)
        assert len(result) == 3

    def test_non_datetime_strict_false_passes(self):
        df = self._valid_ohlc_df()  # RangeIndex
        result = validate_ohlc_data(df, strict=False)
        assert len(result) == 3

    def test_error_message_mentions_index_type(self):
        df = self._valid_ohlc_df()
        with pytest.raises(DataValidationError) as exc_info:
            validate_ohlc_data(df, strict=True)
        assert "RangeIndex" in str(exc_info.value) or "strict=True" in str(exc_info.value)


class TestColumnNameCaseHint:
    """Missing columns error must mention available columns and case hint."""

    def test_missing_col_error_mentions_available(self):
        df = pd.DataFrame({"Open": [1.0], "High": [1.5], "Low": [0.9], "Close": [1.2]})
        with pytest.raises(DataValidationError) as exc_info:
            validate_ohlc_data(df, strict=False)
        msg = str(exc_info.value)
        assert "Available columns" in msg or "open" in msg.lower()


# ===========================================================================
# 3.3 — _defensive_fallback and _make_error_result: INVALID not 0.0
# ===========================================================================

class _DummyGridPoint:
    grid_point_id = "atr10_m2.0_both"
    atr_period = 10
    multiplier = 2.0
    trade_mode = "both"


class _DummyWFSlice:
    step_index = 0
    test_start_idx = 100
    test_end_idx = 200
    train_start_idx = 0
    train_end_idx = 100


class _DummyExtResult:
    metrics = {
        "num_trades": 0,
        "sum_pnl_pct": 0.0,
        "sharpe": INVALID_METRIC_VALUE,
        "sortino": INVALID_METRIC_VALUE,
        "cagr": INVALID_METRIC_VALUE,
        "max_drawdown": INVALID_METRIC_VALUE,
        "win_rate": 0.0,
        "profit_factor": INVALID_METRIC_VALUE,
        "avg_trade": INVALID_METRIC_VALUE,
        "effective_warmup": 0,
    }
    returns = None
    equity_curve = None
    positions = None
    trades_df = None
    early_exit = False
    warmup = 0
    effective_warmup = 0


class TestDefensiveFallbackMetrics:
    """_defensive_fallback must set sum_pnl_pct, win_rate, avg_trade to INVALID."""

    def test_sum_pnl_pct_is_invalid(self):
        sr = _defensive_fallback(
            _DummyExtResult(), _DummyGridPoint(), _DummyWFSlice(), 10, 5,
        )
        assert sr.metrics["sum_pnl_pct"] == INVALID_METRIC_VALUE

    def test_win_rate_is_invalid(self):
        sr = _defensive_fallback(
            _DummyExtResult(), _DummyGridPoint(), _DummyWFSlice(), 10, 5,
        )
        assert sr.metrics["win_rate"] == INVALID_METRIC_VALUE

    def test_avg_trade_is_invalid(self):
        sr = _defensive_fallback(
            _DummyExtResult(), _DummyGridPoint(), _DummyWFSlice(), 10, 5,
        )
        assert sr.metrics["avg_trade"] == INVALID_METRIC_VALUE

    def test_num_trades_is_zero(self):
        """num_trades is the integer count — 0 is correct for a fallback step."""
        sr = _defensive_fallback(
            _DummyExtResult(), _DummyGridPoint(), _DummyWFSlice(), 10, 5,
        )
        assert sr.metrics["num_trades"] == 0

    def test_ratio_metrics_are_invalid(self):
        sr = _defensive_fallback(
            _DummyExtResult(), _DummyGridPoint(), _DummyWFSlice(), 10, 5,
        )
        for key in ("sharpe", "sortino", "cagr", "max_drawdown", "profit_factor"):
            assert sr.metrics[key] == INVALID_METRIC_VALUE, f"{key} should be INVALID"

    def test_used_defensive_fallback_flag(self):
        sr = _defensive_fallback(
            _DummyExtResult(), _DummyGridPoint(), _DummyWFSlice(), 10, 5,
        )
        assert sr.used_defensive_fallback is True

    def test_effective_oos_bars_zero(self):
        sr = _defensive_fallback(
            _DummyExtResult(), _DummyGridPoint(), _DummyWFSlice(), 10, 5,
        )
        assert sr.effective_oos_bars == 0


class TestMakeErrorResultMetrics:
    """_make_error_result must set sum_pnl_pct and win_rate to INVALID."""

    def test_sum_pnl_pct_is_invalid(self):
        sr = _make_error_result(
            _DummyGridPoint(), _DummyWFSlice(), 0, RuntimeError("test"),
        )
        assert sr.metrics["sum_pnl_pct"] == INVALID_METRIC_VALUE

    def test_win_rate_is_invalid(self):
        sr = _make_error_result(
            _DummyGridPoint(), _DummyWFSlice(), 0, RuntimeError("test"),
        )
        assert sr.metrics["win_rate"] == INVALID_METRIC_VALUE

    def test_avg_trade_is_invalid(self):
        sr = _make_error_result(
            _DummyGridPoint(), _DummyWFSlice(), 0, RuntimeError("test"),
        )
        assert sr.metrics["avg_trade"] == INVALID_METRIC_VALUE

    def test_num_trades_is_zero(self):
        sr = _make_error_result(
            _DummyGridPoint(), _DummyWFSlice(), 0, RuntimeError("test"),
        )
        assert sr.metrics["num_trades"] == 0

    def test_error_message_set(self):
        sr = _make_error_result(
            _DummyGridPoint(), _DummyWFSlice(), 0, RuntimeError("boom"),
        )
        assert sr.error_message == "boom"


# ===========================================================================
# 4.3 — compute_score_discrimination: configurable min_passed
# ===========================================================================

class TestScoreDiscriminationConfigurableThreshold:
    """min_passed parameter must replace the hardcoded 5."""

    def _passed_df(self, n: int) -> tuple:
        rows = [{"grid_point_id": f"gp{i}", "sum_pnl_pct_Median": float(i)} for i in range(n)]
        df = pd.DataFrame(rows)
        mask = pd.Series(True, index=df.index)
        return df, mask

    def test_default_threshold_5_with_4_passed_gives_insufficient(self):
        df, mask = self._passed_df(4)
        result = compute_score_discrimination(df, mask)
        assert (result[mask] == "insufficient").all()

    def test_default_threshold_5_with_5_passed_not_insufficient(self):
        df, mask = self._passed_df(5)
        result = compute_score_discrimination(df, mask)
        assert not (result[mask] == "insufficient").any()

    def test_custom_threshold_3_with_3_passed_not_insufficient(self):
        df, mask = self._passed_df(3)
        result = compute_score_discrimination(df, mask, min_passed=3)
        assert not (result[mask] == "insufficient").any()

    def test_custom_threshold_3_with_2_passed_gives_insufficient(self):
        df, mask = self._passed_df(2)
        result = compute_score_discrimination(df, mask, min_passed=3)
        assert (result[mask] == "insufficient").all()

    def test_custom_threshold_10_with_9_gives_insufficient(self):
        df, mask = self._passed_df(9)
        result = compute_score_discrimination(df, mask, min_passed=10)
        assert (result[mask] == "insufficient").all()

    def test_no_passed_returns_all_no_score(self):
        df, _ = self._passed_df(3)
        mask = pd.Series(False, index=df.index)
        result = compute_score_discrimination(df, mask)
        assert (result == "no_score").all()


# ===========================================================================
# 5.6 — legacy mode: seed_gate_passed is primary sort key
# ===========================================================================

def _ranking_cfg(mode: str = "legacy") -> GridConfig:
    return GridConfig(
        data=DataConfig(file_path="dummy.csv"),
        ranking=RankingConfig(mode=mode, sort_by="sum_pnl_pct_Median", tiebreaker="sum_pnl_pct_Min"),
    )


def _ranking_row(
    gp_id: str,
    gate_passed: bool,
    pnl_median: float,
    n_ok: int = 3,
    n_total: int = 3,
) -> dict:
    return {
        "grid_point_id": gp_id,
        "n_ok_steps": n_ok,
        "n_total_steps": n_total,
        "seed_gate_passed": gate_passed,
        "sum_pnl_pct_Median": pnl_median,
        "sum_pnl_pct_Min": pnl_median * 0.5,
        "sum_pnl_pct_Std": 1.0,
        "tester_seed_score": 0.5,
    }


class TestLegacyModeSeedGatePrimarySort:
    """In legacy mode, gate-passed candidates must rank above gate-failed ones."""

    def test_gate_passed_ranks_above_gate_failed_despite_lower_pnl(self):
        """A (passed, pnl=5) must beat B (failed, pnl=20) in legacy mode."""
        df = pd.DataFrame([
            _ranking_row("A", gate_passed=True,  pnl_median=5.0),
            _ranking_row("B", gate_passed=False, pnl_median=20.0),
        ])
        ranked = rank_candidates(df, _ranking_cfg("legacy"))
        rank_a = ranked.loc[ranked["grid_point_id"] == "A", "grid_rank"].iloc[0]
        rank_b = ranked.loc[ranked["grid_point_id"] == "B", "grid_rank"].iloc[0]
        assert rank_a < rank_b, (
            f"Gate-passed A (rank {rank_a}) should rank above gate-failed B (rank {rank_b})"
        )

    def test_within_gate_passed_group_sorted_by_pnl(self):
        """Among gate-passed candidates, sort_by metric determines rank."""
        df = pd.DataFrame([
            _ranking_row("A", gate_passed=True, pnl_median=5.0),
            _ranking_row("B", gate_passed=True, pnl_median=20.0),
        ])
        ranked = rank_candidates(df, _ranking_cfg("legacy"))
        rank_b = ranked.loc[ranked["grid_point_id"] == "B", "grid_rank"].iloc[0]
        rank_a = ranked.loc[ranked["grid_point_id"] == "A", "grid_rank"].iloc[0]
        assert rank_b < rank_a, "Higher pnl_median should rank first within gate-passed group"

    def test_within_gate_failed_group_sorted_by_pnl(self):
        """Among gate-failed candidates, sort_by still determines order within the group."""
        df = pd.DataFrame([
            _ranking_row("A", gate_passed=False, pnl_median=5.0),
            _ranking_row("B", gate_passed=False, pnl_median=20.0),
            _ranking_row("C", gate_passed=True,  pnl_median=1.0),
        ])
        ranked = rank_candidates(df, _ranking_cfg("legacy"))
        rank_c = ranked.loc[ranked["grid_point_id"] == "C", "grid_rank"].iloc[0]
        rank_b = ranked.loc[ranked["grid_point_id"] == "B", "grid_rank"].iloc[0]
        rank_a = ranked.loc[ranked["grid_point_id"] == "A", "grid_rank"].iloc[0]
        # C (passed) must be rank 1; B (failed, higher pnl) before A (failed, lower pnl)
        assert rank_c == 1
        assert rank_b < rank_a

    def test_gates_score_mode_unaffected(self):
        """gates_score mode uses tier-based sorting — no regression from legacy change."""
        df = pd.DataFrame([
            _ranking_row("A", gate_passed=True,  pnl_median=5.0, n_ok=3, n_total=3),
            _ranking_row("B", gate_passed=False, pnl_median=20.0, n_ok=3, n_total=3),
        ])
        ranked = rank_candidates(df, _ranking_cfg("gates_score"))
        # In gates_score mode, Tier 1 (A) still beats Tier 2 (B)
        rank_a = ranked.loc[ranked["grid_point_id"] == "A", "grid_rank"].iloc[0]
        rank_b = ranked.loc[ranked["grid_point_id"] == "B", "grid_rank"].iloc[0]
        assert rank_a < rank_b


# ===========================================================================
# 5.7 — min_total_trades read from YAML
# ===========================================================================

class TestMinTotalTradesFromYAML:
    """gates.candidate.min_total_trades must be loaded from YAML config."""

    def _write_yaml(self, tmp_path: Path, content: str) -> str:
        p = tmp_path / "cfg.yaml"
        p.write_text(textwrap.dedent(content), encoding="utf-8")
        return str(p)

    def test_custom_min_total_trades_loaded(self, tmp_path):
        yaml_str = """\
        data:
          file_path: data.csv
        validation:
          walk_forward:
            train_size: "90D"
            test_size: "30D"
        gates:
          candidate:
            min_total_trades: 50
        """
        path = self._write_yaml(tmp_path, yaml_str)
        cfg = load_grid_config(path)
        assert cfg.gates.candidate.min_total_trades == 50

    def test_default_min_total_trades_is_30(self, tmp_path):
        yaml_str = """\
        data:
          file_path: data.csv
        validation:
          walk_forward:
            train_size: "90D"
            test_size: "30D"
        """
        path = self._write_yaml(tmp_path, yaml_str)
        cfg = load_grid_config(path)
        assert cfg.gates.candidate.min_total_trades == 30

    def test_min_total_trades_zero_valid(self, tmp_path):
        """0 is a valid threshold (effectively disables the gate)."""
        yaml_str = """\
        data:
          file_path: data.csv
        validation:
          walk_forward:
            train_size: "90D"
            test_size: "30D"
        gates:
          candidate:
            min_total_trades: 0
        """
        path = self._write_yaml(tmp_path, yaml_str)
        cfg = load_grid_config(path)
        assert cfg.gates.candidate.min_total_trades == 0

    def test_negative_min_total_trades_raises(self, tmp_path):
        from wf_grid.config.loader import ConfigError
        yaml_str = """\
        data:
          file_path: data.csv
        validation:
          walk_forward:
            train_size: "90D"
            test_size: "30D"
        gates:
          candidate:
            min_total_trades: -1
        """
        path = self._write_yaml(tmp_path, yaml_str)
        with pytest.raises(ConfigError):
            load_grid_config(path)


# ===========================================================================
# 5.8 — apply_candidate_gates: missing ok_ratio / total_oos_trades raises
# ===========================================================================

def _minimal_agg_df(include_ok_ratio: bool = True, include_total_trades: bool = True) -> pd.DataFrame:
    row = {
        "grid_point_id": "gp1",
        "n_ok_steps": 3,
        "n_total_steps": 3,
        "sum_pnl_pct_Median": 5.0,
        "num_trades_Median": 10.0,
        "sum_pnl_pct_Min": 1.0,
        "max_drawdown_Min": -0.20,
    }
    if include_ok_ratio:
        row["ok_ratio"] = 1.0
    if include_total_trades:
        row["total_oos_trades"] = 50
    return pd.DataFrame([row])


def _gates_cfg() -> GridConfig:
    return GridConfig(
        data=DataConfig(file_path="dummy.csv"),
        gates=GatesConfig(
            step=StepGatesConfig(),
            candidate=CandidateGatesConfig(
                min_ok_ratio=0.7,
                min_total_trades=30,
            ),
        ),
    )


class TestGatesMissingRequiredColumns:
    """apply_candidate_gates must raise ValueError if required columns absent."""

    def test_missing_ok_ratio_raises(self):
        df = _minimal_agg_df(include_ok_ratio=False, include_total_trades=True)
        with pytest.raises(ValueError, match="ok_ratio"):
            apply_candidate_gates(df, _gates_cfg())

    def test_missing_total_oos_trades_raises(self):
        df = _minimal_agg_df(include_ok_ratio=True, include_total_trades=False)
        with pytest.raises(ValueError, match="total_oos_trades"):
            apply_candidate_gates(df, _gates_cfg())

    def test_both_present_no_raise(self):
        df = _minimal_agg_df(include_ok_ratio=True, include_total_trades=True)
        result = apply_candidate_gates(df, _gates_cfg())
        assert "gate_ok_coverage" in result.columns
        assert "gate_ok_min_total_trades" in result.columns
