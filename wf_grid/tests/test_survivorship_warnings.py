"""
FIX-5.3 — Tests for survivorship bias warnings.

Three output points are tested:

1. summary_builder — survivorship_warning column
   - computed from ok_ratio with thresholds: >=0.9 → "", >=0.7 → MODERATE,
     >=0.5 → HIGH, <0.5 → CRITICAL
   - column is present in _BLOCK_A immediately after ok_ratio
   - column appears in build_summary_wide output

2. xlsx_writer / DISCLAIMER sheet
   - block with "SURVIVORSHIP BIAS WARNING" heading present
   - key phrases about aggregates, excluded segments, ok_ratio

3. xlsx_writer / WF_Config sheet
   - WARNING section rows present
   - survivorship_bias and ok_ratio_interpretation parameters present

These tests are written RED-first (before FIX-5.3 implementation) and
should PASS after the implementation is complete.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from wf_grid.config.schema import DataConfig, GridConfig, RankingConfig
from wf_grid.export.summary_builder import (
    _BLOCK_A,
    _compute_survivorship_warning,
    build_summary_wide,
)
from wf_grid.export.xlsx_writer import (
    _DISCLAIMER_LINES,
    export_workbook,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg() -> GridConfig:
    return GridConfig(
        data=DataConfig(file_path="dummy.csv"),
        ranking=RankingConfig(mode="gates_score"),
    )


def _make_step_row(gp_id: str, wf_step: int, ok: bool = True) -> dict:
    return {
        "grid_point_id": gp_id,
        "atr_period": 10,
        "multiplier": 2.5,
        "trade_mode": "both",
        "wf_step": wf_step,
        "test_start_idx": (wf_step - 1) * 50,
        "test_end_idx": wf_step * 50,
        "step_status": "ok" if ok else "no_trades",
        "sum_pnl_pct": 3.0,
        "sharpe": 1.0,
        "sortino": 1.2,
        "max_drawdown": -0.08,
        "cagr": 0.10,
        "win_rate": 0.55,
        "num_trades": 5,
        "profit_factor": 1.4,
        "avg_trade": 0.4,
        "prepend_bars_applied": 20,
        "effective_oos_bars": 49,
        "used_prepend": True,
        "used_legacy_oos_path": False,
        "used_defensive_fallback": False,
        "oos_boundary_index": 20,
        "warmup_used": 0,
        "warmup_effective": 0,
        "prepend_bars_requested": 20,
        "error_message": None,
        "error_type": None,
    }


def _make_agg_row(gp_id: str, n_ok: int, n_total: int) -> dict:
    ok_ratio = n_ok / n_total if n_total > 0 else 0.0
    return {
        "grid_point_id": gp_id,
        "n_ok_steps": n_ok,
        "n_total_steps": n_total,
        "ok_ratio": ok_ratio,
        "sum_pnl_pct_Mean": 3.0,
        "sum_pnl_pct_Median": 3.0,
        "sum_pnl_pct_Std": 0.5,
        "sum_pnl_pct_Min": 2.5,
        "sum_pnl_pct_Max": 3.5,
        "num_trades_Median": 5.0,
        "max_drawdown_Min": -0.08,
        "profit_factor_Median": 1.4,
        "sharpe_Median": 1.0,
        "sortino_Median": 1.2,
        "cagr_Median": 0.10,
        "win_rate_Median": 0.55,
        "avg_trade_Median": 0.4,
        "profitable_segments_count": n_ok,
        "total_oos_trades": n_ok * 5,
        "has_defensive_fallback_steps": False,
        "abs_max_drawdown_Min": 0.08,
    }


def _make_ranked_row(gp_id: str, grid_rank: int, n_ok: int, n_total: int) -> dict:
    ok_ratio = n_ok / n_total if n_total > 0 else 0.0
    return {
        "grid_rank": grid_rank,
        "grid_point_id": gp_id,
        "tier": 1,
        "n_ok_steps": n_ok,
        "n_total_steps": n_total,
        "ok_ratio": ok_ratio,
        "seed_gate_passed": True,
        "tester_seed_score": 0.8,
        "score_contract_status": "ok",
        "score_discrimination_status": "ok",
        "gate_ok_positive_median": True,
        "gate_ok_min_trades": True,
        "gate_ok_worst_segment": True,
        "gate_ok_drawdown": True,
        "gate_ok_min_total_trades": True,
        "seed_gate_fail_reason": "",
        "sum_pnl_pct_Median": 3.0,
        "sum_pnl_pct_Min": 2.5,
        "sum_pnl_pct_Std": 0.5,
        "max_drawdown_Min": -0.08,
        "num_trades_Median": 5.0,
        "profit_factor_Median": 1.4,
        "sharpe_Median": 1.0,
        "sortino_Median": 1.2,
        "cagr_Median": 0.10,
        "win_rate_Median": 0.55,
        "avg_trade_Median": 0.4,
        "profitable_segments_count": n_ok,
        "total_oos_trades": n_ok * 5,
        "has_defensive_fallback_steps": False,
        "abs_max_drawdown_Min": 0.08,
    }


def _build_summary(n_ok: int, n_total: int, gp_id: str = "atr10_m2.50_both"):
    """Build summary_wide for one grid point with given ok/total ratio."""
    cfg = _cfg()
    steps = [_make_step_row(gp_id, s, ok=(s <= n_ok)) for s in range(1, n_total + 1)]
    step_df = pd.DataFrame(steps)
    agg_df = pd.DataFrame([_make_agg_row(gp_id, n_ok, n_total)])
    ranked_df = pd.DataFrame([_make_ranked_row(gp_id, 1, n_ok, n_total)])
    return build_summary_wide(step_df, agg_df, ranked_df, cfg)


# ===========================================================================
# 1. Tests for _compute_survivorship_warning helper
# ===========================================================================

class TestComputeSurvivorshipWarning:
    """Unit tests for the _compute_survivorship_warning helper function."""

    def test_full_ok_ratio_no_warning(self):
        assert _compute_survivorship_warning(1.0) == ""

    def test_high_ok_ratio_no_warning(self):
        """ok_ratio >= 0.9 → no warning (single dropped segment acceptable)."""
        assert _compute_survivorship_warning(0.9) == ""

    def test_just_below_threshold_no_warning(self):
        """ok_ratio exactly 0.9 → no warning."""
        assert _compute_survivorship_warning(0.90) == ""

    def test_moderate_upper_boundary(self):
        """ok_ratio just below 0.9 → MODERATE."""
        result = _compute_survivorship_warning(0.89)
        assert result.startswith("MODERATE:"), (
            f"Expected MODERATE: prefix, got: {result!r}"
        )

    def test_moderate_lower_boundary(self):
        """ok_ratio = 0.7 → MODERATE."""
        result = _compute_survivorship_warning(0.70)
        assert result.startswith("MODERATE:"), (
            f"Expected MODERATE: prefix for ok_ratio=0.70, got: {result!r}"
        )

    def test_moderate_contains_percent(self):
        """MODERATE warning includes the excluded percentage."""
        result = _compute_survivorship_warning(0.80)
        assert "20%" in result or "20" in result, (
            f"Expected excluded % in warning, got: {result!r}"
        )

    def test_high_upper_boundary(self):
        """ok_ratio just below 0.7 → HIGH."""
        result = _compute_survivorship_warning(0.69)
        assert result.startswith("HIGH:"), (
            f"Expected HIGH: prefix, got: {result!r}"
        )

    def test_high_lower_boundary(self):
        """ok_ratio = 0.5 → HIGH."""
        result = _compute_survivorship_warning(0.50)
        assert result.startswith("HIGH:"), (
            f"Expected HIGH: prefix for ok_ratio=0.50, got: {result!r}"
        )

    def test_critical_boundary(self):
        """ok_ratio just below 0.5 → CRITICAL."""
        result = _compute_survivorship_warning(0.49)
        assert result.startswith("CRITICAL:"), (
            f"Expected CRITICAL: prefix, got: {result!r}"
        )

    def test_critical_zero(self):
        """ok_ratio = 0.0 → CRITICAL with 100%."""
        result = _compute_survivorship_warning(0.0)
        assert result.startswith("CRITICAL:"), (
            f"Expected CRITICAL: prefix for ok_ratio=0.0, got: {result!r}"
        )
        assert "100%" in result, f"Expected 100% in zero ok_ratio warning, got: {result!r}"

    def test_exact_thresholds_ordering(self):
        """Verify tier ordering is monotonically: >=0.9 → "", >=0.7 → MODERATE, >=0.5 → HIGH, <0.5 → CRITICAL."""
        assert _compute_survivorship_warning(1.00) == ""
        assert _compute_survivorship_warning(0.90) == ""
        assert _compute_survivorship_warning(0.89).startswith("MODERATE")
        assert _compute_survivorship_warning(0.70).startswith("MODERATE")
        assert _compute_survivorship_warning(0.69).startswith("HIGH")
        assert _compute_survivorship_warning(0.50).startswith("HIGH")
        assert _compute_survivorship_warning(0.49).startswith("CRITICAL")
        assert _compute_survivorship_warning(0.00).startswith("CRITICAL")


# ===========================================================================
# 2. Tests for survivorship_warning column in summary_wide
# ===========================================================================

class TestSurvivorshipWarningColumn:
    """Tests for survivorship_warning column in build_summary_wide output."""

    def test_column_present_in_output(self):
        summary = _build_summary(n_ok=4, n_total=4)
        assert "survivorship_warning" in summary.columns, (
            "survivorship_warning column missing from summary_wide output."
        )

    def test_column_in_block_a(self):
        assert "survivorship_warning" in _BLOCK_A, (
            "survivorship_warning must be in _BLOCK_A for prominent display."
        )

    def test_block_a_position_after_ok_ratio(self):
        """survivorship_warning must appear immediately after ok_ratio in _BLOCK_A."""
        ok_ratio_idx = _BLOCK_A.index("ok_ratio")
        sw_idx = _BLOCK_A.index("survivorship_warning")
        assert sw_idx == ok_ratio_idx + 1, (
            f"survivorship_warning is at _BLOCK_A[{sw_idx}], "
            f"expected position {ok_ratio_idx + 1} (immediately after ok_ratio at {ok_ratio_idx})."
        )

    def test_full_ok_ratio_empty_warning(self):
        summary = _build_summary(n_ok=4, n_total=4)
        val = summary["survivorship_warning"].iloc[0]
        assert val == "", (
            f"ok_ratio=1.0 should give empty warning, got: {val!r}"
        )

    def test_moderate_warning_generated(self):
        """ok_ratio = 0.8 (4/5) → MODERATE warning."""
        summary = _build_summary(n_ok=4, n_total=5)
        val = summary["survivorship_warning"].iloc[0]
        assert val.startswith("MODERATE:"), (
            f"ok_ratio=0.8 should produce MODERATE: warning, got: {val!r}"
        )

    def test_high_warning_generated(self):
        """ok_ratio = 0.6 (3/5) → HIGH warning."""
        summary = _build_summary(n_ok=3, n_total=5)
        val = summary["survivorship_warning"].iloc[0]
        assert val.startswith("HIGH:"), (
            f"ok_ratio=0.6 should produce HIGH: warning, got: {val!r}"
        )

    def test_critical_warning_generated(self):
        """ok_ratio = 0.4 (2/5) → CRITICAL warning."""
        summary = _build_summary(n_ok=2, n_total=5)
        val = summary["survivorship_warning"].iloc[0]
        assert val.startswith("CRITICAL:"), (
            f"ok_ratio=0.4 should produce CRITICAL: warning, got: {val!r}"
        )

    def test_warning_before_n_ok_steps_in_output(self):
        """survivorship_warning appears before n_ok_steps in summary columns (Block A ordering)."""
        summary = _build_summary(n_ok=4, n_total=4)
        cols = list(summary.columns)
        assert "survivorship_warning" in cols
        assert "ok_ratio" in cols
        ok_ratio_pos = cols.index("ok_ratio")
        sw_pos = cols.index("survivorship_warning")
        assert sw_pos == ok_ratio_pos + 1, (
            f"survivorship_warning at col {sw_pos}, ok_ratio at col {ok_ratio_pos}; "
            "expected survivorship_warning immediately after ok_ratio."
        )


# ===========================================================================
# 3. Tests for DISCLAIMER sheet survivorship bias block
# ===========================================================================

class TestDisclaimerSurvivorshipWarning:
    """Tests for survivorship bias warning block in _DISCLAIMER_LINES."""

    def _all_text(self) -> str:
        return "\n".join(_DISCLAIMER_LINES)

    def test_survivorship_bias_heading_present(self):
        text = self._all_text()
        assert "SURVIVORSHIP BIAS WARNING" in text, (
            "DISCLAIMER_LINES must contain a 'SURVIVORSHIP BIAS WARNING' heading."
        )

    def test_aggregates_ok_steps_phrase(self):
        """Disclaimer must explain that aggregates use only ok-status steps."""
        text = self._all_text()
        assert "ok" in text.lower() and "step" in text.lower() and "exclud" in text.lower(), (
            "DISCLAIMER must mention that excluded steps affect aggregates."
        )

    def test_ok_ratio_threshold_mentioned(self):
        """Disclaimer must mention ok_ratio threshold for high/critical risk."""
        text = self._all_text()
        assert "ok_ratio" in text or "ok ratio" in text.lower(), (
            "DISCLAIMER must reference ok_ratio in the survivorship bias section."
        )

    def test_survivorship_warning_in_xlsx(self, tmp_path):
        """DISCLAIMER sheet in exported workbook contains the survivorship block."""
        cfg = _cfg()
        n_total = 3
        gp_id = "atr10_m2.50_both"
        steps = [_make_step_row(gp_id, s) for s in range(1, n_total + 1)]
        step_df = pd.DataFrame(steps)
        agg_df = pd.DataFrame([_make_agg_row(gp_id, n_total, n_total)])
        ranked_df = pd.DataFrame([_make_ranked_row(gp_id, 1, n_total, n_total)])
        summary_wide = build_summary_wide(step_df, agg_df, ranked_df, cfg)

        out = tmp_path / "surv_test.xlsx"
        export_workbook(
            summary_wide=summary_wide,
            step_oos_long=step_df,
            trades_oos=pd.DataFrame(),
            trades_train=pd.DataFrame(),
            config=cfg,
            output_path=out,
        )

        disc_df = pd.read_excel(out, sheet_name="DISCLAIMER")
        text = " ".join(disc_df["Text"].fillna("").astype(str).tolist())
        assert "SURVIVORSHIP BIAS WARNING" in text, (
            "DISCLAIMER sheet in XLSX must contain 'SURVIVORSHIP BIAS WARNING'."
        )


# ===========================================================================
# 4. Tests for WF_Config WARNING section
# ===========================================================================

class TestWFConfigWarningSection:
    """Tests for WARNING section rows in WF_Config sheet."""

    def _get_config_df(self, tmp_path) -> pd.DataFrame:
        cfg = _cfg()
        n_total = 3
        gp_id = "atr10_m2.50_both"
        steps = [_make_step_row(gp_id, s) for s in range(1, n_total + 1)]
        step_df = pd.DataFrame(steps)
        agg_df = pd.DataFrame([_make_agg_row(gp_id, n_total, n_total)])
        ranked_df = pd.DataFrame([_make_ranked_row(gp_id, 1, n_total, n_total)])
        summary_wide = build_summary_wide(step_df, agg_df, ranked_df, cfg)

        out = tmp_path / "config_test.xlsx"
        export_workbook(
            summary_wide=summary_wide,
            step_oos_long=step_df,
            trades_oos=pd.DataFrame(),
            trades_train=pd.DataFrame(),
            config=cfg,
            output_path=out,
        )
        return pd.read_excel(out, sheet_name="WF_Config")

    def test_warning_section_exists(self, tmp_path):
        df = self._get_config_df(tmp_path)
        sections = df["Section"].fillna("").astype(str).tolist()
        assert "WARNING" in sections, (
            "WF_Config sheet must contain at least one row with Section='WARNING'."
        )

    def test_survivorship_bias_parameter_present(self, tmp_path):
        df = self._get_config_df(tmp_path)
        warning_rows = df[df["Section"].fillna("").astype(str) == "WARNING"]
        params = warning_rows["Parameter"].fillna("").astype(str).tolist()
        assert "survivorship_bias" in params, (
            "WF_Config WARNING section must contain Parameter='survivorship_bias'."
        )

    def test_ok_ratio_interpretation_parameter_present(self, tmp_path):
        df = self._get_config_df(tmp_path)
        warning_rows = df[df["Section"].fillna("").astype(str) == "WARNING"]
        params = warning_rows["Parameter"].fillna("").astype(str).tolist()
        assert "ok_ratio_interpretation" in params, (
            "WF_Config WARNING section must contain Parameter='ok_ratio_interpretation'."
        )

    def test_warning_values_non_empty(self, tmp_path):
        df = self._get_config_df(tmp_path)
        warning_rows = df[df["Section"].fillna("").astype(str) == "WARNING"]
        for _, row in warning_rows.iterrows():
            assert str(row["Value"]).strip() != "", (
                f"WARNING row '{row['Parameter']}' has empty Value."
            )
