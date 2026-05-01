"""
FIX-5.5 — Tests for Pipeline Phase marker.

Three output points are tested:

1. _PIPELINE_VERSION contains phase marker ("phaseA" / "Phase A" / similar)

2. _DISCLAIMER_LINES contains a PIPELINE PHASE STATUS block:
   - heading "PIPELINE PHASE STATUS"
   - "Phase A" mentioned as current
   - "Phase B" mentioned as missing
   - prohibition on real money without next phases

3. WF_Config PHASE section in exported workbook:
   - Section "PHASE" present
   - Parameters: current_phase, missing_phases, recommendation

All tests are written RED-first (before FIX-5.5 implementation).
"""

from __future__ import annotations

import pandas as pd
import pytest

from wf_grid.config.schema import DataConfig, GridConfig, RankingConfig
from wf_grid.export.summary_builder import build_summary_wide
from wf_grid.export.xlsx_writer import (
    _DISCLAIMER_LINES,
    _PIPELINE_VERSION,
    export_workbook,
)


# ---------------------------------------------------------------------------
# Helpers (minimal export fixture)
# ---------------------------------------------------------------------------

def _cfg() -> GridConfig:
    return GridConfig(
        data=DataConfig(file_path="dummy.csv"),
        ranking=RankingConfig(mode="gates_score"),
    )


def _make_step_row(gp_id: str, wf_step: int) -> dict:
    return {
        "grid_point_id": gp_id,
        "atr_period": 10,
        "multiplier": 2.5,
        "trade_mode": "both",
        "wf_step": wf_step,
        "test_start_idx": (wf_step - 1) * 50,
        "test_end_idx": wf_step * 50,
        "step_status": "ok",
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


def _make_agg_row(gp_id: str) -> dict:
    return {
        "grid_point_id": gp_id,
        "n_ok_steps": 3, "n_total_steps": 3, "ok_ratio": 1.0,
        "sum_pnl_pct_Mean": 3.0, "sum_pnl_pct_Median": 3.0,
        "sum_pnl_pct_Std": 0.5, "sum_pnl_pct_Min": 2.5, "sum_pnl_pct_Max": 3.5,
        "num_trades_Median": 5.0, "max_drawdown_Min": -0.08,
        "profit_factor_Median": 1.4, "sharpe_Median": 1.0,
        "sortino_Median": 1.2, "cagr_Median": 0.10,
        "win_rate_Median": 0.55, "avg_trade_Median": 0.4,
        "profitable_segments_count": 3, "total_oos_trades": 15,
        "has_defensive_fallback_steps": False, "abs_max_drawdown_Min": 0.08,
    }


def _make_ranked_row(gp_id: str) -> dict:
    return {
        "grid_rank": 1, "grid_point_id": gp_id, "tier": 1,
        "n_ok_steps": 3, "n_total_steps": 3, "ok_ratio": 1.0,
        "seed_gate_passed": True, "tester_seed_score": 0.8,
        "score_contract_status": "ok", "score_discrimination_status": "ok",
        "gate_ok_positive_median": True, "gate_ok_min_trades": True,
        "gate_ok_worst_segment": True, "gate_ok_drawdown": True,
        "gate_ok_min_total_trades": True, "seed_gate_fail_reason": "",
        "sum_pnl_pct_Median": 3.0, "sum_pnl_pct_Min": 2.5,
        "sum_pnl_pct_Std": 0.5, "max_drawdown_Min": -0.08,
        "num_trades_Median": 5.0, "profit_factor_Median": 1.4,
        "sharpe_Median": 1.0, "sortino_Median": 1.2,
        "cagr_Median": 0.10, "win_rate_Median": 0.55, "avg_trade_Median": 0.4,
        "profitable_segments_count": 3, "total_oos_trades": 15,
        "has_defensive_fallback_steps": False, "abs_max_drawdown_Min": 0.08,
    }


def _export_workbook(tmp_path) -> tuple:
    """Return (out_path, disc_df, config_df)."""
    cfg = _cfg()
    gp_id = "atr10_m2.50_both"
    steps = [_make_step_row(gp_id, s) for s in range(1, 4)]
    step_df = pd.DataFrame(steps)
    agg_df = pd.DataFrame([_make_agg_row(gp_id)])
    ranked_df = pd.DataFrame([_make_ranked_row(gp_id)])
    summary_wide = build_summary_wide(step_df, agg_df, ranked_df, cfg)

    out = tmp_path / "phase_test.xlsx"
    export_workbook(
        summary_wide=summary_wide,
        step_oos_long=step_df,
        trades_oos=pd.DataFrame(),
        trades_train=pd.DataFrame(),
        config=cfg,
        output_path=out,
    )
    disc_df = pd.read_excel(out, sheet_name="DISCLAIMER")
    config_df = pd.read_excel(out, sheet_name="WF_Config")
    return out, disc_df, config_df


# ===========================================================================
# 1. Tests for _PIPELINE_VERSION phase marker
# ===========================================================================

class TestPipelineVersion:
    """_PIPELINE_VERSION must contain a phase marker."""

    def test_version_contains_phase_marker(self):
        version_lower = _PIPELINE_VERSION.lower()
        assert "phase" in version_lower or "pha" in version_lower, (
            f"_PIPELINE_VERSION '{_PIPELINE_VERSION}' does not contain a phase marker. "
            "Expected something like '2.0-phaseA'."
        )

    def test_version_contains_phase_a(self):
        version_lower = _PIPELINE_VERSION.lower()
        assert "a" in version_lower, (
            f"_PIPELINE_VERSION '{_PIPELINE_VERSION}' does not mention Phase A."
        )

    def test_version_line_in_disclaimer_contains_phase(self, tmp_path):
        """The 'Pipeline version:' line in DISCLAIMER sheet contains the phase marker."""
        _, disc_df, _ = _export_workbook(tmp_path)
        text_vals = disc_df["Text"].fillna("").astype(str).tolist()
        version_lines = [t for t in text_vals if "Pipeline version" in t]
        assert version_lines, "No 'Pipeline version:' line found in DISCLAIMER sheet."
        version_line = version_lines[0]
        assert "phase" in version_line.lower() or "Phase" in version_line, (
            f"Version line '{version_line}' does not contain phase marker."
        )


# ===========================================================================
# 2. Tests for PIPELINE PHASE STATUS block in _DISCLAIMER_LINES
# ===========================================================================

class TestDisclaimerPhaseBlock:
    """DISCLAIMER must contain a PIPELINE PHASE STATUS block."""

    def _text(self) -> str:
        return "\n".join(_DISCLAIMER_LINES)

    def test_phase_status_heading_present(self):
        text = self._text()
        assert "PIPELINE PHASE STATUS" in text, (
            "DISCLAIMER must contain a 'PIPELINE PHASE STATUS' heading."
        )

    def test_phase_a_current_mentioned(self):
        text = self._text()
        assert "Phase A" in text or "phase A" in text.lower(), (
            "DISCLAIMER must state that Phase A is current."
        )

    def test_phase_b_missing_mentioned(self):
        text = self._text()
        assert "Phase B" in text or "phase B" in text.lower(), (
            "DISCLAIMER must state that Phase B is missing."
        )

    def test_phase_b_content_described(self):
        """Phase B content (regime analysis / CI / benchmark) must be mentioned."""
        text = self._text()
        assert "regime" in text.lower() or "confidence" in text.lower() or "benchmark" in text.lower(), (
            "DISCLAIMER must describe what Phase B includes."
        )

    def test_real_money_prohibition_without_phase_b(self):
        text = self._text()
        lower = text.lower()
        has_prohibition = (
            ("do not" in lower or "don't" in lower or "without" in lower)
            and ("real money" in lower or "real-money" in lower or "phase b" in lower)
        )
        assert has_prohibition, (
            "DISCLAIMER must contain a prohibition on real-money use without Phase B."
        )

    def test_phase_block_in_xlsx(self, tmp_path):
        """DISCLAIMER sheet in XLSX contains the phase status block."""
        _, disc_df, _ = _export_workbook(tmp_path)
        text = " ".join(disc_df["Text"].fillna("").astype(str).tolist())
        assert "PIPELINE PHASE STATUS" in text, (
            "DISCLAIMER sheet in XLSX must contain 'PIPELINE PHASE STATUS'."
        )
        assert "Phase A" in text, (
            "DISCLAIMER sheet must mention 'Phase A' as current."
        )
        assert "Phase B" in text, (
            "DISCLAIMER sheet must mention 'Phase B' as missing."
        )


# ===========================================================================
# 3. Tests for PHASE section in WF_Config
# ===========================================================================

class TestWFConfigPhaseSection:
    """WF_Config sheet must contain a PHASE section."""

    def test_phase_section_exists(self, tmp_path):
        _, _, config_df = _export_workbook(tmp_path)
        sections = config_df["Section"].fillna("").astype(str).tolist()
        assert "PHASE" in sections, (
            "WF_Config sheet must contain at least one row with Section='PHASE'."
        )

    def test_current_phase_parameter_present(self, tmp_path):
        _, _, config_df = _export_workbook(tmp_path)
        phase_rows = config_df[config_df["Section"].fillna("").astype(str) == "PHASE"]
        params = phase_rows["Parameter"].fillna("").astype(str).tolist()
        assert "current_phase" in params, (
            "WF_Config PHASE section must contain Parameter='current_phase'."
        )

    def test_missing_phases_parameter_present(self, tmp_path):
        _, _, config_df = _export_workbook(tmp_path)
        phase_rows = config_df[config_df["Section"].fillna("").astype(str) == "PHASE"]
        params = phase_rows["Parameter"].fillna("").astype(str).tolist()
        assert "missing_phases" in params, (
            "WF_Config PHASE section must contain Parameter='missing_phases'."
        )

    def test_recommendation_parameter_present(self, tmp_path):
        _, _, config_df = _export_workbook(tmp_path)
        phase_rows = config_df[config_df["Section"].fillna("").astype(str) == "PHASE"]
        params = phase_rows["Parameter"].fillna("").astype(str).tolist()
        assert "recommendation" in params, (
            "WF_Config PHASE section must contain Parameter='recommendation'."
        )

    def test_current_phase_value_is_a(self, tmp_path):
        _, _, config_df = _export_workbook(tmp_path)
        phase_rows = config_df[config_df["Section"].fillna("").astype(str) == "PHASE"]
        current = phase_rows[phase_rows["Parameter"] == "current_phase"]["Value"]
        assert len(current) > 0, "current_phase row missing."
        val = str(current.iloc[0])
        assert "A" in val or "a" in val.lower(), (
            f"current_phase value '{val}' must indicate Phase A."
        )

    def test_phase_values_non_empty(self, tmp_path):
        _, _, config_df = _export_workbook(tmp_path)
        phase_rows = config_df[config_df["Section"].fillna("").astype(str) == "PHASE"]
        for _, row in phase_rows.iterrows():
            assert str(row["Value"]).strip() not in ("", "nan"), (
                f"PHASE row '{row['Parameter']}' has empty Value."
            )
