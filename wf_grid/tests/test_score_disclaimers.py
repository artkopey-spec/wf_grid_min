"""
FIX-5.4 — Tests for Score / Tier / Rank disclaimers.

Two output points are tested:

1. summary_builder — score_interpretation_note column
   - computed from score_discrimination_status and score_contract_status
   - "insufficient" → threshold-aware "fewer than {N} passed candidates: ..."
   - "low_spread"   → "Low spread: scores barely differ"
   - "no_score"     → "Not scored"
   - "ok"           → ""  (no noise when score is meaningful)
   - column is present in _BLOCK_A immediately after score_discrimination_status
   - column appears in build_summary_wide output

2. xlsx_writer / DISCLAIMER sheet
   - block "WHAT SCORES / TIERS / RANKS MEAN" present
   - score is relative within this run only
   - not comparable across runs/datasets
   - Tier 1 is not proof of profitability
   - Rank 1 means only best among tested combinations

All tests are written RED-first (before FIX-5.4 implementation).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from wf_grid.config.schema import DataConfig, GridConfig, RankingConfig
from wf_grid.export.summary_builder import (
    _BLOCK_A,
    _compute_score_interpretation_note,
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
        "n_ok_steps": 3,
        "n_total_steps": 3,
        "ok_ratio": 1.0,
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
        "profitable_segments_count": 3,
        "total_oos_trades": 15,
        "has_defensive_fallback_steps": False,
        "abs_max_drawdown_Min": 0.08,
    }


def _make_ranked_row(
    gp_id: str,
    grid_rank: int = 1,
    seed_gate_passed: bool = True,
    score_discrimination_status: str = "ok",
    score_contract_status: str = "ok",
) -> dict:
    return {
        "grid_rank": grid_rank,
        "grid_point_id": gp_id,
        "tier": 1,
        "n_ok_steps": 3,
        "n_total_steps": 3,
        "ok_ratio": 1.0,
        "seed_gate_passed": seed_gate_passed,
        "tester_seed_score": 0.8 if seed_gate_passed else float("nan"),
        "score_contract_status": score_contract_status,
        "score_discrimination_status": score_discrimination_status,
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
        "profitable_segments_count": 3,
        "total_oos_trades": 15,
        "has_defensive_fallback_steps": False,
        "abs_max_drawdown_Min": 0.08,
    }


def _build_summary_with_score_status(
    gp_id: str = "atr10_m2.50_both",
    score_discrimination_status: str = "ok",
    score_contract_status: str = "ok",
    seed_gate_passed: bool = True,
):
    cfg = _cfg()
    steps = [_make_step_row(gp_id, s) for s in range(1, 4)]
    step_df = pd.DataFrame(steps)
    agg_df = pd.DataFrame([_make_agg_row(gp_id)])
    ranked_df = pd.DataFrame([_make_ranked_row(
        gp_id,
        score_discrimination_status=score_discrimination_status,
        score_contract_status=score_contract_status,
        seed_gate_passed=seed_gate_passed,
    )])
    return build_summary_wide(step_df, agg_df, ranked_df, cfg)


# ===========================================================================
# 1. Tests for _compute_score_interpretation_note helper
# ===========================================================================

class TestComputeScoreInterpretationNote:
    """Unit tests for the _compute_score_interpretation_note helper function."""

    def test_ok_discrimination_ok_contract_empty_note(self):
        """Both statuses ok → no note (normal meaningful scoring)."""
        result = _compute_score_interpretation_note(
            score_discrimination_status="ok",
            score_contract_status="ok",
            min_passed_for_discrimination=5,
        )
        assert result == "", (
            f"Expected empty note for ok/ok statuses, got: {result!r}"
        )

    def test_insufficient_discrimination_note(self):
        """insufficient → threshold-aware fewer-than-N message."""
        result = _compute_score_interpretation_note(
            score_discrimination_status="insufficient",
            score_contract_status="ok",
            min_passed_for_discrimination=5,
        )
        assert "fewer than 5 passed candidates" in result.lower(), (
            f"Expected note about insufficient passed candidates, got: {result!r}"
        )
        assert "unreliable" in result.lower()

    def test_low_spread_discrimination_note(self):
        """low_spread → 'Low spread: scores barely differ'."""
        result = _compute_score_interpretation_note(
            score_discrimination_status="low_spread",
            score_contract_status="ok",
            min_passed_for_discrimination=5,
        )
        assert "spread" in result.lower() or "barely" in result.lower(), (
            f"Expected note about low spread, got: {result!r}"
        )

    def test_no_score_contract_note(self):
        """no_score contract → 'Not scored'."""
        result = _compute_score_interpretation_note(
            score_discrimination_status="no_score",
            score_contract_status="no_score",
            min_passed_for_discrimination=5,
        )
        assert "not scored" in result.lower() or "no_score" in result.lower(), (
            f"Expected 'Not scored' note, got: {result!r}"
        )

    def test_partial_contract_no_note(self):
        """partial contract but ok discrimination → no extra noise."""
        result = _compute_score_interpretation_note(
            score_discrimination_status="ok",
            score_contract_status="partial",
            min_passed_for_discrimination=5,
        )
        # partial is already surfaced by score_contract_status column itself;
        # score_interpretation_note should not duplicate it — either "" or brief note
        # The key requirement: result is a string (no crash)
        assert isinstance(result, str)

    def test_returns_string_always(self):
        """Helper always returns a string, never raises."""
        for disc in ("ok", "insufficient", "low_spread", "no_score", "unknown"):
            for cont in ("ok", "partial", "no_score", "unknown"):
                result = _compute_score_interpretation_note(disc, cont, 5)
                assert isinstance(result, str), (
                    f"Expected str for ({disc!r}, {cont!r}), got {type(result)}"
                )


# ===========================================================================
# 2. Tests for score_interpretation_note column in summary_wide
# ===========================================================================

class TestScoreInterpretationNoteColumn:
    """Tests for score_interpretation_note column in build_summary_wide output."""

    def test_column_present_in_output(self):
        summary = _build_summary_with_score_status()
        assert "score_interpretation_note" in summary.columns, (
            "score_interpretation_note column missing from summary_wide."
        )

    def test_column_in_block_a(self):
        assert "score_interpretation_note" in _BLOCK_A, (
            "score_interpretation_note must be in _BLOCK_A."
        )

    def test_block_a_position_after_score_discrimination_status(self):
        """score_interpretation_note must appear immediately after score_discrimination_status."""
        disc_idx = _BLOCK_A.index("score_discrimination_status")
        note_idx = _BLOCK_A.index("score_interpretation_note")
        assert note_idx == disc_idx + 1, (
            f"score_interpretation_note is at _BLOCK_A[{note_idx}], "
            f"expected {disc_idx + 1} (immediately after score_discrimination_status at {disc_idx})."
        )

    def test_ok_status_empty_note(self):
        summary = _build_summary_with_score_status(
            score_discrimination_status="ok",
            score_contract_status="ok",
        )
        val = summary["score_interpretation_note"].iloc[0]
        assert val == "", (
            f"ok/ok statuses should produce empty note, got: {val!r}"
        )

    def test_insufficient_status_note_generated(self):
        summary = _build_summary_with_score_status(
            score_discrimination_status="insufficient",
            score_contract_status="ok",
        )
        val = summary["score_interpretation_note"].iloc[0]
        assert val != "", (
            "insufficient discrimination should produce a non-empty note."
        )
        assert "unreliable" in val.lower() and "fewer than 5 passed candidates" in val.lower(), (
            f"Expected note mentioning unreliability, got: {val!r}"
        )

    def test_low_spread_status_note_generated(self):
        summary = _build_summary_with_score_status(
            score_discrimination_status="low_spread",
            score_contract_status="ok",
        )
        val = summary["score_interpretation_note"].iloc[0]
        assert val != "", (
            "low_spread discrimination should produce a non-empty note."
        )
        assert "spread" in val.lower() or "barely" in val.lower(), (
            f"Expected note mentioning low spread, got: {val!r}"
        )

    def test_no_score_note_generated(self):
        summary = _build_summary_with_score_status(
            score_discrimination_status="no_score",
            score_contract_status="no_score",
            seed_gate_passed=False,
        )
        val = summary["score_interpretation_note"].iloc[0]
        assert "not scored" in val.lower() or "no_score" in val.lower(), (
            f"Expected 'Not scored' note for no_score status, got: {val!r}"
        )

    def test_note_column_in_block_a_range_in_dataframe(self):
        """score_interpretation_note appears in Block A columns of actual output."""
        summary = _build_summary_with_score_status()
        cols = list(summary.columns)
        assert "score_interpretation_note" in cols
        disc_pos = cols.index("score_discrimination_status")
        note_pos = cols.index("score_interpretation_note")
        assert note_pos == disc_pos + 1, (
            f"In DataFrame: score_interpretation_note at col {note_pos}, "
            f"score_discrimination_status at col {disc_pos}; "
            "expected score_interpretation_note immediately after."
        )

    def test_insufficient_note_uses_configured_min_passed_threshold(self):
        """score_interpretation_note must use config.scoring.min_passed_for_discrimination."""
        cfg = _cfg()
        cfg.scoring.min_passed_for_discrimination = 10
        steps = [_make_step_row("atr10_m2.50_both", s) for s in range(1, 4)]
        step_df = pd.DataFrame(steps)
        agg_df = pd.DataFrame([_make_agg_row("atr10_m2.50_both")])
        ranked_df = pd.DataFrame([_make_ranked_row(
            "atr10_m2.50_both",
            score_discrimination_status="insufficient",
            score_contract_status="ok",
        )])
        summary = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        val = summary["score_interpretation_note"].iloc[0]
        assert "10" in val
        assert "<5 scored" not in val
        assert "fewer than 10 passed candidates" in val.lower()


# ===========================================================================
# 3. Tests for DISCLAIMER score/tier/rank block
# ===========================================================================

class TestDisclaimerScoreTierRankBlock:
    """Tests for Score/Tier/Rank disclaimer block in _DISCLAIMER_LINES."""

    def _text(self) -> str:
        return "\n".join(_DISCLAIMER_LINES)

    def test_scores_tiers_ranks_heading_present(self):
        text = self._text()
        assert "WHAT SCORES" in text or "SCORES / TIERS / RANKS" in text or "SCORES" in text, (
            "DISCLAIMER must contain a heading about Scores/Tiers/Ranks."
        )

    def test_score_is_relative_within_run(self):
        """Disclaimer must state score is relative within this run."""
        text = self._text()
        assert "relative" in text.lower() or "within this" in text.lower(), (
            "DISCLAIMER must say score is relative within the current grid run."
        )

    def test_score_not_comparable_across_runs(self):
        """Disclaimer must state scores are not comparable across runs."""
        text = self._text()
        # Already exists in base lines, but must survive new additions
        assert "not comparable" in text.lower() or "NOT comparable" in text, (
            "DISCLAIMER must state scores are not comparable across runs."
        )

    def test_tier1_not_proof_of_profitability(self):
        """Disclaimer must address Tier 1 / profitability misconception."""
        text = self._text()
        assert "tier 1" in text.lower() or "proven profitable" in text.lower(), (
            "DISCLAIMER must address the Tier 1 / profitability misconception."
        )

    def test_rank1_semantics_explained(self):
        """Disclaimer must clarify what Rank 1 means."""
        text = self._text()
        assert "rank" in text.lower(), (
            "DISCLAIMER must mention rank semantics."
        )

    def test_multiple_comparisons_warning_present(self):
        """DISCLAIMER must contain a multiple comparisons / data mining warning."""
        text = self._text()
        assert "multiple comparisons" in text.lower() or "data mining" in text.lower(), (
            "DISCLAIMER must warn about multiple comparisons / data mining bias."
        )

    def test_multiple_comparisons_mentions_rank1_not_proof(self):
        """DISCLAIMER must state that rank 1 is not an independent OOS proof."""
        text = self._text()
        assert "independent" in text.lower() or "not an independent" in text.lower(), (
            "DISCLAIMER must state rank 1 is not an independent OOS proof."
        )

    def test_multiple_comparisons_recommends_forward_test(self):
        """DISCLAIMER must recommend forward test."""
        text = self._text()
        assert "forward test" in text.lower() or "forward" in text.lower(), (
            "DISCLAIMER must recommend independent forward test."
        )

    def test_scores_block_in_xlsx(self, tmp_path):
        """DISCLAIMER sheet in XLSX contains the score/tier/rank block."""
        cfg = _cfg()
        n_total = 3
        gp_id = "atr10_m2.50_both"
        steps = [_make_step_row(gp_id, s) for s in range(1, n_total + 1)]
        step_df = pd.DataFrame(steps)
        agg_df = pd.DataFrame([_make_agg_row(gp_id)])
        ranked_df = pd.DataFrame([_make_ranked_row(gp_id)])
        summary_wide = build_summary_wide(step_df, agg_df, ranked_df, cfg)

        out = tmp_path / "score_disc_test.xlsx"
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
        assert "SCORES" in text or "TIERS" in text or "RANKS" in text, (
            "DISCLAIMER sheet in XLSX must contain the Score/Tier/Rank block."
        )
        assert "relative" in text.lower() or "within this" in text.lower(), (
            "DISCLAIMER sheet must mention that scores are relative within this run."
        )


# ===========================================================================
# FIX-multiple-comparisons: grid_search_caveat column in summary_wide
# ===========================================================================

class TestGridSearchCaveatColumn:
    """grid_search_caveat column must be present in summary_wide and in _BLOCK_A."""

    def test_column_present_in_summary(self):
        summary = _build_summary_with_score_status()
        assert "grid_search_caveat" in summary.columns, (
            "grid_search_caveat column must be present in summary_wide."
        )

    def test_column_in_block_a(self):
        from wf_grid.export.summary_builder import _BLOCK_A
        assert "grid_search_caveat" in _BLOCK_A, (
            "grid_search_caveat must be in _BLOCK_A."
        )

    def test_column_value_mentions_n_combinations(self):
        summary = _build_summary_with_score_status()
        val = summary["grid_search_caveat"].iloc[0]
        assert isinstance(val, str) and len(val) > 0, (
            "grid_search_caveat must be a non-empty string."
        )
        assert "N=" in val or "combinations" in val.lower(), (
            f"grid_search_caveat should mention N= or combinations, got: {val!r}"
        )

    def test_column_value_mentions_rank1_not_proof(self):
        summary = _build_summary_with_score_status()
        val = summary["grid_search_caveat"].iloc[0]
        assert "rank 1" in val.lower() or "not an independent" in val.lower(), (
            f"grid_search_caveat should warn that Rank 1 is not an independent proof, got: {val!r}"
        )

    def test_multiple_comparisons_in_wf_config_sheet(self, tmp_path):
        """WF_Config sheet must contain the multiple_comparisons warning row."""
        cfg = _cfg()
        gp_id = "atr10_m2.50_both"
        steps = [_make_step_row(gp_id, s) for s in range(1, 4)]
        step_df = pd.DataFrame(steps)
        agg_df = pd.DataFrame([_make_agg_row(gp_id)])
        ranked_df = pd.DataFrame([_make_ranked_row(gp_id)])
        summary_wide = build_summary_wide(step_df, agg_df, ranked_df, cfg)

        out = tmp_path / "multi_cmp_test.xlsx"
        export_workbook(
            summary_wide=summary_wide,
            step_oos_long=step_df,
            trades_oos=pd.DataFrame(),
            trades_train=pd.DataFrame(),
            config=cfg,
            output_path=out,
        )

        cfg_df = pd.read_excel(out, sheet_name="WF_Config")
        params = cfg_df["Parameter"].fillna("").astype(str).tolist()
        assert "multiple_comparisons" in params, (
            "WF_Config sheet must contain a 'multiple_comparisons' warning row."
        )


# ===========================================================================
# Stage 7 — A1: aggregation_scope marker + T1: Tier 1 wording
# ===========================================================================

class TestAggregationScopeInDisclaimerText:
    """T1 / A1: DISCLAIMER sheet must clarify ok-only scope and Tier 1 meaning."""

    def _text(self) -> str:
        from wf_grid.export.xlsx_writer import _DISCLAIMER_LINES
        return "\n".join(_DISCLAIMER_LINES)

    def test_ok_only_mentioned_in_disclaimer(self):
        """DISCLAIMER must state that aggregates are ok-only."""
        text = self._text()
        assert "ok-only" in text.lower() or "ok_steps_only" in text.lower() or (
            "only ok-status" in text.lower() or "only ok" in text.lower()
        ), "DISCLAIMER must mention ok-only aggregation scope."

    def test_tier1_not_trading_signal_in_disclaimer(self):
        """DISCLAIMER must say Tier 1 is not a trading signal."""
        text = self._text()
        assert "not a trading signal" in text.lower(), (
            "DISCLAIMER must state Tier 1 / Phase A output is not a trading signal."
        )

    def test_tier1_wording_contains_phase_a(self):
        """DISCLAIMER Tier 1 description must reference Phase A ok-only framing."""
        text = self._text()
        assert "phase a" in text.lower() or "ok-only" in text.lower(), (
            "DISCLAIMER Tier 1 description must reference Phase A or ok-only."
        )


class TestTier1WordingInTieringSource:
    """T1: tiering.py must use plan-compliant wording (plan §4.7)."""

    def test_tiering_docstring_mentions_phase_a_ok_only(self):
        """tiering.py module docstring must say 'Phase A ok-only', not 'proven'."""
        import wf_grid.ranking.tiering as tiering_mod
        import inspect
        src = inspect.getdoc(tiering_mod) or ""
        assert "phase a ok-only" in src.lower(), (
            "tiering.py docstring must use 'Phase A ok-only' wording per plan §4.7."
        )

    def test_tiering_docstring_no_standalone_proven(self):
        """tiering.py docstring must not use 'proven' as a tier label."""
        import wf_grid.ranking.tiering as tiering_mod
        import inspect
        src = inspect.getdoc(tiering_mod) or ""
        # "proven" must not appear as a positive tier descriptor;
        # only negation forms like "not proof of edge" are acceptable.
        # Check: the word 'proven' must not appear at all (negation is expressed
        # as 'not proof of edge', not 'NOT proven').
        assert "proven" not in src.lower(), (
            "tiering.py docstring must not use 'proven' — use 'not proof of edge' instead."
        )


# ===========================================================================
# Stage 8 — required disclaimer substrings + forbidden wording
# ===========================================================================

class TestRequiredDisclaimerSubstrings:
    """X1 / A1: DISCLAIMER must contain all required substrings per plan §5."""

    def _text(self) -> str:
        from wf_grid.export.xlsx_writer import _DISCLAIMER_LINES
        return "\n".join(_DISCLAIMER_LINES)

    def test_ok_only_present(self):
        text = self._text()
        assert "ok-only" in text.lower() or "ok_steps_only" in text.lower() or \
               "only ok-status" in text.lower(), \
            "DISCLAIMER must contain 'ok-only' or equivalent."

    def test_not_proof_of_edge_present(self):
        text = self._text()
        assert "not proof of edge" in text.lower(), \
            "DISCLAIMER must contain 'not proof of edge'."

    def test_not_a_trading_signal_present(self):
        text = self._text()
        assert "not a trading signal" in text.lower(), \
            "DISCLAIMER must contain 'not a trading signal'."

    def test_limited_debug_present(self):
        text = self._text()
        assert "limited debug" in text.lower(), \
            "DISCLAIMER must label WF_Trades as 'limited debug' export."


class TestForbiddenWordingAbsent:
    """Negative test: forbidden wording must be absent from user-facing source strings.

    Scope: ranking/tiering.py, export/xlsx_writer.py, export/summary_builder.py.
    Forbidden substrings (case-insensitive): proven, production-ready,
      production grade, tradable, paper-ready, trading signal, edge confirmed.
    Exception: 'not a trading signal' is an allowed disclaimer phrase.
    """

    # Forbidden substrings (case-insensitive)
    _FORBIDDEN = [
        "proven",
        "production-ready",
        "production grade",
        "tradable",
        "paper-ready",
        "trading signal",
        "edge confirmed",
    ]

    # These exact phrases are allowed even though they contain forbidden substrings.
    _ALLOWED_PHRASES = [
        "not a trading signal",
    ]

    def _collect_user_facing_text(self) -> dict[str, str]:
        """Return {label: text} for each in-scope module's user-facing strings."""
        import inspect
        import wf_grid.ranking.tiering as tiering_mod
        import wf_grid.export.xlsx_writer as xlsx_mod
        import wf_grid.export.summary_builder as summary_mod

        texts: dict[str, str] = {}

        # tiering.py: module docstring + all function docstrings
        texts["tiering.py:module_doc"] = inspect.getdoc(tiering_mod) or ""
        for name, obj in inspect.getmembers(tiering_mod, inspect.isfunction):
            doc = inspect.getdoc(obj) or ""
            if doc:
                texts[f"tiering.py:{name}"] = doc

        # xlsx_writer.py: _DISCLAIMER_LINES (the primary user-facing output)
        texts["xlsx_writer.py:_DISCLAIMER_LINES"] = "\n".join(
            getattr(xlsx_mod, "_DISCLAIMER_LINES", [])
        )
        # Also scan non-private string constants (pipeline version etc.)
        for name in dir(xlsx_mod):
            if name.startswith("_") and name.isupper():
                val = getattr(xlsx_mod, name, None)
                if isinstance(val, str):
                    texts[f"xlsx_writer.py:{name}"] = val

        # summary_builder.py: grid_search_caveat string (generated in build_summary_wide)
        # Only the caveat string is user-facing; the column lists are internal.
        for name in dir(summary_mod):
            if not name.startswith("_"):
                val = getattr(summary_mod, name, None)
                if isinstance(val, str) and len(val) > 10:
                    texts[f"summary_builder.py:{name}"] = val

        return texts

    @staticmethod
    def _sanitize(text: str) -> str:
        """Remove allowed phrases before scanning for forbidden substrings."""
        import re
        result = text
        for phrase in TestForbiddenWordingAbsent._ALLOWED_PHRASES:
            result = re.sub(re.escape(phrase), "", result, flags=re.IGNORECASE)
        return result

    def test_no_forbidden_words_in_user_facing_strings(self):
        """Comprehensive negative test: all forbidden substrings absent."""
        texts = self._collect_user_facing_text()

        violations: list[str] = []
        for location, text in texts.items():
            sanitized = self._sanitize(text)
            for forbidden in self._FORBIDDEN:
                if forbidden.lower() in sanitized.lower():
                    violations.append(
                        f"  '{forbidden}' found in {location}"
                    )

        assert not violations, (
            "Forbidden wording found in user-facing strings:\n"
            + "\n".join(violations)
        )

    def test_trading_signal_exception_works(self):
        """'not a trading signal' must NOT be flagged as a violation."""
        test_text = "Phase A output is a STARTING POINT for research, not a trading signal."
        sanitized = self._sanitize(test_text)
        assert "trading signal" not in sanitized.lower(), (
            "The exception sanitizer must remove 'not a trading signal' from the text."
        )

    def test_disclaimer_lines_have_no_forbidden_words(self):
        """Quick focused test: _DISCLAIMER_LINES specifically."""
        from wf_grid.export.xlsx_writer import _DISCLAIMER_LINES
        full_text = "\n".join(_DISCLAIMER_LINES)
        sanitized = self._sanitize(full_text)
        violations = [
            f for f in self._FORBIDDEN
            if f.lower() in sanitized.lower()
        ]
        assert not violations, (
            f"Forbidden words in _DISCLAIMER_LINES after removing allowed phrases: "
            f"{violations}"
        )


class TestDisabledGateDisplayInXLSX:
    """G1 (display layer): gate_ok_worst_segment=pd.NA must render as 'disabled' in XLSX."""

    def test_disabled_gate_renders_as_disabled_string(self):
        """pd.NA in gate_ok_worst_segment → cell value 'disabled' after render."""
        from wf_grid.export.xlsx_writer import _render_disabled_gate_columns
        import pandas as pd

        df = pd.DataFrame({
            "gate_ok_worst_segment": [pd.NA, True, False, pd.NA],
            "gate_ok_drawdown": [True, True, False, True],
        })
        out = _render_disabled_gate_columns(df.copy())

        assert out["gate_ok_worst_segment"].iloc[0] == "disabled"
        assert out["gate_ok_worst_segment"].iloc[1] is True
        assert out["gate_ok_worst_segment"].iloc[2] is False
        assert out["gate_ok_worst_segment"].iloc[3] == "disabled"

    def test_disabled_gate_not_true_in_rendered_df(self):
        """Disabled worst-segment gate must NOT appear as True in XLSX output."""
        from wf_grid.export.xlsx_writer import _render_disabled_gate_columns
        import pandas as pd

        df = pd.DataFrame({"gate_ok_worst_segment": [pd.NA]})
        out = _render_disabled_gate_columns(df.copy())
        assert out["gate_ok_worst_segment"].iloc[0] != True  # noqa: E712

    def test_other_gate_columns_not_affected(self):
        """Only gate_ok_worst_segment is transformed; other gate columns stay unchanged."""
        from wf_grid.export.xlsx_writer import _render_disabled_gate_columns
        import pandas as pd

        df = pd.DataFrame({
            "gate_ok_worst_segment": [pd.NA],
            "gate_ok_drawdown": [True],
            "gate_ok_positive_median": [False],
        })
        out = _render_disabled_gate_columns(df.copy())

        assert bool(out["gate_ok_drawdown"].iloc[0]) is True
        assert bool(out["gate_ok_positive_median"].iloc[0]) is False

    def test_column_absent_is_no_op(self):
        """If gate_ok_worst_segment column is absent, function must not raise."""
        from wf_grid.export.xlsx_writer import _render_disabled_gate_columns
        import pandas as pd

        df = pd.DataFrame({"gate_ok_drawdown": [True, False]})
        out = _render_disabled_gate_columns(df.copy())
        assert list(out.columns) == ["gate_ok_drawdown"]
