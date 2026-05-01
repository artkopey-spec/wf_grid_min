"""
Unit tests for A5: step_collector — collect_oos_steps, collect_train_steps.

Covers:
- Schema: all required columns present, in correct order.
- Completeness invariant: |rows| == |grid_points| * |wf_steps|.
- Uniqueness invariant: (grid_point_id, wf_step) distinct.
- Diagnostic fields in OOS: used_prepend, used_legacy_oos_path, etc.
- step_status assignment: ok / no_trades / runtime_error.
- Error step handling: error_message / error_type propagated.
- Sort order: (grid_point_id, wf_step) ASC.
- Empty grid: returns empty DataFrame with correct columns.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import pytest

from wf_grid.collect.step_collector import (
    CollectionError,
    _OOS_COLUMNS,
    _OOS_COLUMNS_BASE,
    _TRAIN_COLUMNS,
    _TRAIN_COLUMNS_BASE,
    collect_oos_steps,
    collect_train_steps,
)
from wf_grid.config.loader import load_grid_config
from wf_grid.config.schema import GridConfig, INVALID_METRIC_VALUE
from wf_grid.grid.enumeration import GridPoint
from wf_grid.status.status_model import StepStatus
from wf_grid.wf.step_executor import StepResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(tmp_path: Path, content: str) -> str:
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return str(p)


def _config(tmp_path, min_meaningful_bars: int = 5) -> GridConfig:
    yaml_text = f"""\
data:
  file_path: data.csv
  periods_per_year: 252
validation:
  walk_forward:
    train_size: "200bars"
    test_size: "50bars"
status:
  min_meaningful_bars: {min_meaningful_bars}
"""
    path = _write_yaml(tmp_path, yaml_text)
    cfg = load_grid_config(path)
    cfg.resolved_periods_per_year = 252.0
    return cfg


def _ok_metrics() -> Dict[str, Any]:
    return {
        "num_trades": 5,
        "sum_pnl_pct": 2.5,
        "sharpe": 1.2,
        "sortino": 1.5,
        "max_drawdown": -0.10,
        "cagr": 0.15,
        "win_rate": 60.0,
        "profit_factor": 1.8,
        "avg_trade": 0.5,
        "net_pnl_pct": 0.5,
    }


def _make_step_result(
    gp_id: str,
    wf_step: int,
    metrics: Optional[Dict[str, Any]] = None,
    effective_oos_bars: int = 50,
    used_prepend: bool = True,
    used_legacy: bool = False,
    used_defensive_fallback: bool = False,
    error_message: Optional[str] = None,
    error_type: Optional[str] = None,
) -> StepResult:
    if metrics is None:
        metrics = _ok_metrics()
    return StepResult(
        grid_point_id=gp_id,
        wf_step=wf_step,
        test_start_idx=200 + wf_step * 50,
        test_end_idx=250 + wf_step * 50,
        metrics=metrics,
        oos_trades_df=None,
        prepend_bars_requested=55,
        prepend_bars_applied=55,
        used_prepend=used_prepend,
        used_legacy_oos_path=used_legacy,
        used_defensive_fallback=used_defensive_fallback,
        oos_boundary_index=55,
        warmup_used=0,
        warmup_effective=0,
        effective_oos_bars=effective_oos_bars,
        early_exit=False,
        error_message=error_message,
        error_type=error_type,
    )


def _grid_results(n_gp: int = 2, n_steps: int = 3) -> Dict[str, List[StepResult]]:
    """Build a simple grid_results dict with ok steps."""
    return {
        f"atr{5+i}_m2.00_both": [
            _make_step_result(f"atr{5+i}_m2.00_both", step)
            for step in range(n_steps)
        ]
        for i in range(n_gp)
    }


# ===========================================================================
# Schema — all required columns present in correct order
# ===========================================================================

class TestOosSchema:
    def test_all_columns_present(self, tmp_path):
        """Without filter summary in StepResults, only base columns are present."""
        cfg = _config(tmp_path)
        gr = _grid_results(2, 2)
        df = collect_oos_steps(gr, cfg)
        for col in _OOS_COLUMNS_BASE:
            assert col in df.columns, f"Missing column: {col}"

    def test_column_order(self, tmp_path):
        """Without filter summary in StepResults, columns == base columns only."""
        cfg = _config(tmp_path)
        gr = _grid_results(2, 2)
        df = collect_oos_steps(gr, cfg)
        assert list(df.columns) == _OOS_COLUMNS_BASE

    def test_diagnostic_columns_present(self, tmp_path):
        cfg = _config(tmp_path)
        gr = _grid_results(1, 1)
        df = collect_oos_steps(gr, cfg)
        diag = ["used_prepend", "used_legacy_oos_path", "prepend_bars_requested",
                "prepend_bars_applied", "oos_boundary_index", "warmup_used",
                "warmup_effective", "effective_oos_bars"]
        for col in diag:
            assert col in df.columns


class TestTrainSchema:
    def test_all_train_columns_present(self, tmp_path):
        """Without filter summary in StepResults, only base columns are present."""
        cfg = _config(tmp_path)
        gr = _grid_results(2, 2)
        df = collect_train_steps(gr, cfg)
        for col in _TRAIN_COLUMNS_BASE:
            assert col in df.columns

    def test_train_column_order(self, tmp_path):
        """Without filter summary in StepResults, columns == base columns only."""
        cfg = _config(tmp_path)
        gr = _grid_results(2, 2)
        df = collect_train_steps(gr, cfg)
        assert list(df.columns) == _TRAIN_COLUMNS_BASE

    def test_train_no_prepend_diagnostic_columns(self, tmp_path):
        cfg = _config(tmp_path)
        gr = _grid_results(1, 1)
        df = collect_train_steps(gr, cfg)
        # train does not have prepend-specific columns
        assert "used_prepend" not in df.columns
        assert "prepend_bars_requested" not in df.columns


# ===========================================================================
# Completeness invariant (§11.1)
# ===========================================================================

class TestCompleteness:
    def test_row_count_matches_grid_times_steps(self, tmp_path):
        cfg = _config(tmp_path)
        gr = _grid_results(n_gp=3, n_steps=4)
        df = collect_oos_steps(gr, cfg)
        assert len(df) == 3 * 4

    def test_completeness_with_explicit_expected(self, tmp_path):
        cfg = _config(tmp_path)
        gr = _grid_results(n_gp=2, n_steps=3)
        df = collect_oos_steps(gr, cfg, expected_n_steps=3)
        assert len(df) == 6

    def test_completeness_error_when_steps_mismatch(self, tmp_path):
        cfg = _config(tmp_path)
        gr = _grid_results(n_gp=2, n_steps=3)
        with pytest.raises(CollectionError, match="completeness"):
            # claim 5 expected steps but only 3 present
            collect_oos_steps(gr, cfg, expected_n_steps=5)

    def test_empty_grid_returns_empty_df(self, tmp_path):
        cfg = _config(tmp_path)
        df = collect_oos_steps({}, cfg)
        assert len(df) == 0
        assert list(df.columns) == _OOS_COLUMNS_BASE

    def test_single_grid_point_single_step(self, tmp_path):
        cfg = _config(tmp_path)
        gr = {"atr5_m2.00_both": [_make_step_result("atr5_m2.00_both", 0)]}
        df = collect_oos_steps(gr, cfg)
        assert len(df) == 1


# ===========================================================================
# Uniqueness invariant (§11.1)
# ===========================================================================

class TestUniqueness:
    def test_no_duplicate_keys(self, tmp_path):
        cfg = _config(tmp_path)
        gr = _grid_results(3, 5)
        df = collect_oos_steps(gr, cfg)
        assert not df.duplicated(subset=["grid_point_id", "wf_step"]).any()

    def test_duplicate_raises_error(self, tmp_path):
        cfg = _config(tmp_path)
        # Inject duplicate: same gp_id, same wf_step
        sr0 = _make_step_result("atr5_m2.00_both", 0)
        sr0_dup = _make_step_result("atr5_m2.00_both", 0)
        gr = {"atr5_m2.00_both": [sr0, sr0_dup]}
        with pytest.raises(CollectionError, match="uniqueness"):
            collect_oos_steps(gr, cfg)


# ===========================================================================
# step_status assignment
# ===========================================================================

class TestStepStatusAssignment:
    def test_ok_status_for_valid_step(self, tmp_path):
        cfg = _config(tmp_path, min_meaningful_bars=5)
        gr = {"atr5_m2.00_both": [_make_step_result("atr5_m2.00_both", 0,
                                                      effective_oos_bars=50)]}
        df = collect_oos_steps(gr, cfg)
        assert df.iloc[0]["step_status"] == StepStatus.OK.value

    def test_no_trades_status(self, tmp_path):
        cfg = _config(tmp_path, min_meaningful_bars=5)
        m = _ok_metrics()
        m["num_trades"] = 0
        gr = {"atr5_m2.00_both": [_make_step_result("atr5_m2.00_both", 0,
                                                      metrics=m, effective_oos_bars=50)]}
        df = collect_oos_steps(gr, cfg)
        assert df.iloc[0]["step_status"] == StepStatus.NO_TRADES.value

    def test_insufficient_bars_status(self, tmp_path):
        cfg = _config(tmp_path, min_meaningful_bars=30)
        gr = {"atr5_m2.00_both": [_make_step_result("atr5_m2.00_both", 0,
                                                      effective_oos_bars=10)]}
        df = collect_oos_steps(gr, cfg)
        assert df.iloc[0]["step_status"] == StepStatus.INSUFFICIENT_BARS.value

    def test_runtime_error_status_not_overwritten(self, tmp_path):
        """error_message present → runtime_error, NOT overwritten by assign_step_status."""
        cfg = _config(tmp_path, min_meaningful_bars=5)
        # Metrics look 'ok' — but error_message is set, so status must be runtime_error
        sr = _make_step_result("atr5_m2.00_both", 0,
                               effective_oos_bars=50,
                               error_message="division by zero",
                               error_type="ZeroDivisionError")
        gr = {"atr5_m2.00_both": [sr]}
        df = collect_oos_steps(gr, cfg)
        assert df.iloc[0]["step_status"] == StepStatus.RUNTIME_ERROR.value


# ===========================================================================
# Diagnostic fields propagated to DataFrame
# ===========================================================================

class TestDiagnosticFields:
    def test_used_prepend_propagated(self, tmp_path):
        cfg = _config(tmp_path)
        sr = _make_step_result("atr5_m2.00_both", 0, used_prepend=True)
        gr = {"atr5_m2.00_both": [sr]}
        df = collect_oos_steps(gr, cfg)
        assert bool(df.iloc[0]["used_prepend"]) is True

    def test_used_legacy_propagated(self, tmp_path):
        cfg = _config(tmp_path)
        sr = _make_step_result("atr5_m2.00_both", 0,
                               used_prepend=False, used_legacy=True)
        gr = {"atr5_m2.00_both": [sr]}
        df = collect_oos_steps(gr, cfg)
        assert bool(df.iloc[0]["used_legacy_oos_path"]) is True
        assert bool(df.iloc[0]["used_prepend"]) is False

    def test_prepend_bars_values_propagated(self, tmp_path):
        cfg = _config(tmp_path)
        sr = _make_step_result("atr5_m2.00_both", 0)
        sr.prepend_bars_requested = 100
        sr.prepend_bars_applied = 80
        gr = {"atr5_m2.00_both": [sr]}
        df = collect_oos_steps(gr, cfg)
        row = df.iloc[0]
        assert row["prepend_bars_requested"] == 100
        assert row["prepend_bars_applied"] == 80

    def test_error_fields_propagated(self, tmp_path):
        cfg = _config(tmp_path)
        sr = _make_step_result("atr5_m2.00_both", 0,
                               error_message="Some error", error_type="ValueError")
        gr = {"atr5_m2.00_both": [sr]}
        df = collect_oos_steps(gr, cfg)
        assert df.iloc[0]["error_message"] == "Some error"
        assert df.iloc[0]["error_type"] == "ValueError"

    def test_no_error_fields_none_for_ok_step(self, tmp_path):
        cfg = _config(tmp_path)
        gr = {"atr5_m2.00_both": [_make_step_result("atr5_m2.00_both", 0)]}
        df = collect_oos_steps(gr, cfg)
        assert df.iloc[0]["error_message"] is None
        assert df.iloc[0]["error_type"] is None


# ===========================================================================
# Sort order
# ===========================================================================

class TestSortOrder:
    def test_sorted_by_gp_id_then_wf_step(self, tmp_path):
        cfg = _config(tmp_path)
        gr = _grid_results(n_gp=3, n_steps=3)
        df = collect_oos_steps(gr, cfg)
        expected = df.sort_values(["grid_point_id", "wf_step"])
        pd.testing.assert_frame_equal(df.reset_index(drop=True),
                                       expected.reset_index(drop=True))

    def test_wf_steps_in_order_per_gp(self, tmp_path):
        cfg = _config(tmp_path)
        # Provide steps out of order to ensure sorting is applied
        gp = "atr5_m2.00_both"
        steps = [
            _make_step_result(gp, 2),
            _make_step_result(gp, 0),
            _make_step_result(gp, 1),
        ]
        gr = {gp: steps}
        df = collect_oos_steps(gr, cfg)
        assert list(df["wf_step"]) == [0, 1, 2]


# ===========================================================================
# Metrics values correct in DataFrame
# ===========================================================================

class TestMetricValues:
    def test_sum_pnl_pct_stored(self, tmp_path):
        cfg = _config(tmp_path)
        m = _ok_metrics()
        m["sum_pnl_pct"] = 7.77
        gr = {"atr5_m2.00_both": [_make_step_result("atr5_m2.00_both", 0, metrics=m)]}
        df = collect_oos_steps(gr, cfg)
        assert df.iloc[0]["sum_pnl_pct"] == pytest.approx(7.77)

    def test_max_drawdown_sign_preserved(self, tmp_path):
        cfg = _config(tmp_path)
        m = _ok_metrics()
        m["max_drawdown"] = -0.25
        gr = {"atr5_m2.00_both": [_make_step_result("atr5_m2.00_both", 0, metrics=m)]}
        df = collect_oos_steps(gr, cfg)
        assert df.iloc[0]["max_drawdown"] == pytest.approx(-0.25)

    def test_invalid_metric_value_propagated(self, tmp_path):
        cfg = _config(tmp_path)
        m = _ok_metrics()
        m["sharpe"] = INVALID_METRIC_VALUE
        gr = {"atr5_m2.00_both": [_make_step_result("atr5_m2.00_both", 0, metrics=m)]}
        df = collect_oos_steps(gr, cfg)
        assert df.iloc[0]["sharpe"] == INVALID_METRIC_VALUE


# ===========================================================================
# grid_point_id as authoritative identity key (reviewer note)
# ===========================================================================

class TestIdentityKey:
    def test_grid_point_id_present_all_rows(self, tmp_path):
        cfg = _config(tmp_path)
        gr = _grid_results(4, 3)
        df = collect_oos_steps(gr, cfg)
        assert df["grid_point_id"].notna().all()

    def test_identity_pair_covers_all_combinations(self, tmp_path):
        cfg = _config(tmp_path)
        gr = _grid_results(2, 3)
        df = collect_oos_steps(gr, cfg)
        pairs = set(zip(df["grid_point_id"], df["wf_step"]))
        expected = {
            (gp_id, step)
            for gp_id, steps in gr.items()
            for step in range(len(steps))
        }
        assert pairs == expected


# ===========================================================================
# FIX-1.2 — used_defensive_fallback propagated through collection
# ===========================================================================

class TestUsedDefensiveFallbackColumn:
    """FIX-1.2: used_defensive_fallback must be present in _OOS_COLUMNS and in DataFrame."""

    def test_schema_contains_used_defensive_fallback(self):
        """Schema test: used_defensive_fallback in _OOS_COLUMNS after used_legacy_oos_path."""
        assert "used_defensive_fallback" in _OOS_COLUMNS

    def test_used_defensive_fallback_position_after_used_legacy(self):
        """used_defensive_fallback must appear immediately after used_legacy_oos_path."""
        idx_legacy = _OOS_COLUMNS.index("used_legacy_oos_path")
        idx_defensive = _OOS_COLUMNS.index("used_defensive_fallback")
        assert idx_defensive == idx_legacy + 1

    def test_defensive_fallback_true_propagated(self, tmp_path):
        """Step with used_defensive_fallback=True → column value is True in DataFrame."""
        cfg = _config(tmp_path)
        sr = _make_step_result("atr5_m2.00_both", 0, used_defensive_fallback=True)
        gr = {"atr5_m2.00_both": [sr]}
        df = collect_oos_steps(gr, cfg)
        assert bool(df.iloc[0]["used_defensive_fallback"]) is True

    def test_defensive_fallback_false_propagated(self, tmp_path):
        """Step with used_defensive_fallback=False → column value is False in DataFrame."""
        cfg = _config(tmp_path)
        sr = _make_step_result("atr5_m2.00_both", 0, used_defensive_fallback=False)
        gr = {"atr5_m2.00_both": [sr]}
        df = collect_oos_steps(gr, cfg)
        assert bool(df.iloc[0]["used_defensive_fallback"]) is False

    def test_mixed_steps_defensive_fallback_values(self, tmp_path):
        """Multiple steps: each row has its own used_defensive_fallback value."""
        cfg = _config(tmp_path)
        sr0 = _make_step_result("atr5_m2.00_both", 0, used_defensive_fallback=False)
        sr1 = _make_step_result("atr5_m2.00_both", 1, used_defensive_fallback=True)
        sr2 = _make_step_result("atr5_m2.00_both", 2, used_defensive_fallback=False)
        gr = {"atr5_m2.00_both": [sr0, sr1, sr2]}
        df = collect_oos_steps(gr, cfg).sort_values("wf_step").reset_index(drop=True)
        assert bool(df.iloc[0]["used_defensive_fallback"]) is False
        assert bool(df.iloc[1]["used_defensive_fallback"]) is True
        assert bool(df.iloc[2]["used_defensive_fallback"]) is False

    def test_column_order_intact_with_new_field(self, tmp_path):
        """Without filter summary, column order must match _OOS_COLUMNS_BASE exactly."""
        cfg = _config(tmp_path)
        gr = _grid_results(1, 1)
        df = collect_oos_steps(gr, cfg)
        assert list(df.columns) == _OOS_COLUMNS_BASE


# ===========================================================================
# FIX-3.3 — Structured identity columns from GridPoint
# ===========================================================================

class TestStructuredIdentityColumns:
    """FIX-3.3: atr_period, multiplier, trade_mode populated from GridPoint objects."""

    def _make_grid_points(self):
        return [
            GridPoint(atr_period=5, multiplier=2.0, trade_mode="both",
                      grid_point_id="atr5_m2.00_both"),
            GridPoint(atr_period=6, multiplier=2.0, trade_mode="both",
                      grid_point_id="atr6_m2.00_both"),
        ]

    def test_identity_columns_in_oos_schema(self):
        assert "atr_period" in _OOS_COLUMNS
        assert "multiplier" in _OOS_COLUMNS
        assert "trade_mode" in _OOS_COLUMNS

    def test_identity_columns_in_train_schema(self):
        assert "atr_period" in _TRAIN_COLUMNS
        assert "multiplier" in _TRAIN_COLUMNS
        assert "trade_mode" in _TRAIN_COLUMNS

    def test_identity_columns_order_after_grid_point_id(self):
        """atr_period, multiplier, trade_mode come right after grid_point_id."""
        idx_gp = _OOS_COLUMNS.index("grid_point_id")
        assert _OOS_COLUMNS[idx_gp + 1] == "atr_period"
        assert _OOS_COLUMNS[idx_gp + 2] == "multiplier"
        assert _OOS_COLUMNS[idx_gp + 3] == "trade_mode"

    def test_oos_populated_from_grid_points(self, tmp_path):
        cfg = _config(tmp_path)
        gps = self._make_grid_points()
        gr = _grid_results(n_gp=2, n_steps=2)
        df = collect_oos_steps(gr, cfg, grid_points=gps)
        row0 = df[df["grid_point_id"] == "atr5_m2.00_both"].iloc[0]
        assert row0["atr_period"] == 5
        assert row0["multiplier"] == pytest.approx(2.0)
        assert row0["trade_mode"] == "both"

    def test_oos_without_grid_points_has_none(self, tmp_path):
        cfg = _config(tmp_path)
        gr = _grid_results(n_gp=1, n_steps=1)
        df = collect_oos_steps(gr, cfg)
        assert df.iloc[0]["atr_period"] is None
        assert df.iloc[0]["multiplier"] is None
        assert df.iloc[0]["trade_mode"] is None

    def test_train_populated_from_grid_points(self, tmp_path):
        cfg = _config(tmp_path)
        gps = self._make_grid_points()
        gr = _grid_results(n_gp=2, n_steps=2)
        df = collect_train_steps(gr, cfg, grid_points=gps)
        row1 = df[df["grid_point_id"] == "atr6_m2.00_both"].iloc[0]
        assert row1["atr_period"] == 6
        assert row1["multiplier"] == pytest.approx(2.0)
        assert row1["trade_mode"] == "both"

    def test_all_rows_have_identity(self, tmp_path):
        """When grid_points provided, all rows must have non-None identity values."""
        cfg = _config(tmp_path)
        gps = self._make_grid_points()
        gr = _grid_results(n_gp=2, n_steps=3)
        df = collect_oos_steps(gr, cfg, grid_points=gps)
        assert df["atr_period"].notna().all()
        assert df["multiplier"].notna().all()
        assert df["trade_mode"].notna().all()

    def test_identity_values_correct_per_grid_point(self, tmp_path):
        """Each row's identity matches its grid_point_id's GridPoint."""
        cfg = _config(tmp_path)
        gps = self._make_grid_points()
        gr = _grid_results(n_gp=2, n_steps=2)
        df = collect_oos_steps(gr, cfg, grid_points=gps)
        for _, row in df.iterrows():
            gp = next(g for g in gps if g.grid_point_id == row["grid_point_id"])
            assert row["atr_period"] == gp.atr_period
            assert row["multiplier"] == pytest.approx(gp.multiplier)
            assert row["trade_mode"] == gp.trade_mode
