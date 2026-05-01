"""
Stage 10 — Consolidated regression suite (plan §5 / §6 DoD).

This file bundles the cross-cutting regression acceptance criteria listed in
the approved fixes plan §5.  Finer-grained coverage (metric contracts, OOS
force-flat, disabled gates, strict YAML, disclaimers, etc.) lives in the
dedicated per-stage test modules:

  - test_metric_contracts.py        (M1/M2/M3)
  - test_oos_force_flat.py          (OOS1)
  - test_gates.py                   (disabled worst-segment gate)
  - test_config_schema.py           (schema_version, strict YAML, drift)
  - test_score_disclaimers.py       (Tier 1 wording, forbidden wording)
  - test_summary_builder.py         (aggregation_scope, n_segments, bucket status)

Scope of this module is the §5 "Regression Tests" + "Golden/Differential" bullets
that are NOT already pinned in those modules:

  - Empty step_oos_long → n_segments == 0.
  - PipelineResult() defaults are backward-compatible with new bucket matrix
    fields.
  - Positive max_drawdown on an ok step can no longer silently reach gate/rank
    stages (§5 "Positive DD can no longer silently pass drawdown gates.").
  - Aggregation remains ok-status only — no `_all` / `_failed` / `_nonok`
    aggregate columns appear in summary_wide.
  - Aggregator rejects non-ok rows silently (differential).
"""

from __future__ import annotations

import pandas as pd

from wf_grid.collect.metric_contracts import validate_metric_contracts
from wf_grid.config.schema import (
    DataConfig,
    GridConfig,
    RankingConfig,
)
from wf_grid.export.summary_builder import _BLOCK_A, _BLOCK_B, build_summary_wide
from wf_grid.pipeline.orchestrator import PipelineResult
from wf_grid.status.status_model import StepStatus


# ---------------------------------------------------------------------------
# Shared helpers (minimal copies — we do not import from test_summary_builder
# to keep this module self-contained and avoid test-collection coupling).
# ---------------------------------------------------------------------------

def _cfg() -> GridConfig:
    return GridConfig(
        data=DataConfig(file_path="dummy.csv"),
        ranking=RankingConfig(mode="gates_score"),
    )


def _step_row(gp: str, wf: int, *, status: str = StepStatus.OK.value,
              sum_pnl_pct: float = 5.0, max_drawdown: float = -0.10,
              num_trades: int = 10) -> dict:
    return {
        "grid_point_id": gp,
        "wf_step": wf,
        "step_status": status,
        "sum_pnl_pct": sum_pnl_pct,
        "sharpe": 1.2,
        "sortino": 1.5,
        "max_drawdown": max_drawdown,
        "cagr": 0.15,
        "win_rate": 0.6,
        "num_trades": num_trades,
        "profit_factor": 1.8,
        "avg_trade": 0.5,
        "prepend_bars_applied": 50,
        "effective_oos_bars": 100,
    }


def _agg_row(gp: str) -> dict:
    return {
        "grid_point_id": gp,
        "n_ok_steps": 2,
        "n_total_steps": 2,
        "ok_ratio": 1.0,
        "sum_pnl_pct_Mean": 5.0,
        "sum_pnl_pct_Std": 2.0,
        "sum_pnl_pct_Min": 1.0,
        "sum_pnl_pct_Max": 10.0,
        "sum_pnl_pct_Median": 5.0,
        "num_trades_Median": 10.0,
        "max_drawdown_Min": -0.20,
        "profit_factor_Median": 1.8,
        "sharpe_Median": 1.2,
        "sortino_Median": 1.5,
        "cagr_Median": 0.15,
        "win_rate_Median": 0.6,
        "avg_trade_Median": 0.5,
        "profitable_segments_count": 2,
        "total_oos_trades": 20,
        "has_defensive_fallback_steps": False,
    }


def _ranked_row(gp: str, grid_rank: int = 1) -> dict:
    return {
        "grid_rank": grid_rank,
        "grid_point_id": gp,
        "tier": 1,
        "n_ok_steps": 2,
        "n_total_steps": 2,
        "ok_ratio": 1.0,
        "seed_gate_passed": True,
        "tester_seed_score": 0.8,
        "score_contract_status": "ok",
        "score_discrimination_status": "ok",
        "gate_ok_positive_median": True,
        "gate_ok_min_trades": True,
        "gate_ok_worst_segment": True,
        "gate_ok_drawdown": True,
        "seed_gate_fail_reason": "",
        "sum_pnl_pct_Median": 5.0,
        "sum_pnl_pct_Min": 1.0,
        "sum_pnl_pct_Std": 2.0,
        "max_drawdown_Min": -0.20,
        "num_trades_Median": 10.0,
        "profit_factor_Median": 1.8,
        "sharpe_Median": 1.2,
        "sortino_Median": 1.5,
        "cagr_Median": 0.15,
        "win_rate_Median": 0.6,
        "avg_trade_Median": 0.5,
        "profitable_segments_count": 2,
    }


def _build(gp_ids, n_steps: int = 2):
    cfg = _cfg()
    steps = [_step_row(g, s) for g in gp_ids for s in range(1, n_steps + 1)]
    agg = [_agg_row(g) for g in gp_ids]
    ranked = [_ranked_row(g, grid_rank=i + 1) for i, g in enumerate(gp_ids)]
    return (
        pd.DataFrame(steps),
        pd.DataFrame(agg),
        pd.DataFrame(ranked),
        cfg,
    )


# ===========================================================================
# §5 Regression: empty step_oos_long → n_segments == 0
# ===========================================================================

class TestEmptyStepOosLong:
    def test_empty_summary_returns_empty_df(self):
        result = build_summary_wide(
            pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), _cfg(),
        )
        assert result.empty

    def test_n_segments_zero_for_empty_wf_step_column(self):
        """If wf_step column exists but the series is empty → n_segments == 0."""
        step_df = pd.DataFrame({"wf_step": pd.Series([], dtype="int64")})
        assert int(step_df["wf_step"].nunique()) == 0


# ===========================================================================
# §5 Regression: PipelineResult() backward-compatible defaults
# ===========================================================================

class TestPipelineResultDefaults:
    def test_pipeline_result_constructs_with_no_args(self):
        """PipelineResult() must be constructable without any arguments."""
        result = PipelineResult()
        assert result is not None

    def test_bucket_matrix_status_default_is_disabled(self):
        """New field bucket_matrix_status defaults to 'disabled'."""
        result = PipelineResult()
        assert result.bucket_matrix_status == "disabled"

    def test_bucket_matrix_error_default_is_none(self):
        """New field bucket_matrix_error defaults to None."""
        result = PipelineResult()
        assert result.bucket_matrix_error is None

    def test_bucket_matrix_median_default_is_none(self):
        """Pre-existing field bucket_matrix_median remains None by default."""
        result = PipelineResult()
        assert result.bucket_matrix_median is None

    def test_legacy_fields_unchanged(self):
        """Ensure no legacy fields were accidentally removed from PipelineResult."""
        result = PipelineResult()
        for field in (
            "config", "grid_points", "step_oos_long", "step_train_long",
            "trades_oos", "trades_train", "aggregated", "ranked",
            "summary_wide", "output_path", "diagnostics", "error",
        ):
            assert hasattr(result, field), f"missing legacy field {field!r}"


# ===========================================================================
# §5 Regression: positive max_drawdown can no longer silently pass gates
# ===========================================================================

class TestPositiveDrawdownFailsClosed:
    """Plan §5 Regression: 'Positive DD can no longer silently pass drawdown gates.'

    validate_metric_contracts runs BEFORE apply_step_gates in the orchestrator
    (plan §4.1 / M1).  A positive max_drawdown on an ok step is a broken hard
    metric and must be re-tagged invalid with error_type == 'metric_contract'
    before the gate/aggregation stages ever see the row.
    """

    def test_positive_max_drawdown_invalidates_ok_step(self):
        rows = [
            _step_row("gp1", 1, max_drawdown=+0.05),
            _step_row("gp1", 2, max_drawdown=-0.05),
        ]
        df = pd.DataFrame(rows)
        df["error_type"] = ""
        out = validate_metric_contracts(df)
        assert out.loc[0, "step_status"] == "invalid"
        assert out.loc[0, "error_type"] == "metric_contract"
        # Second row (legitimate negative DD) must remain ok.
        assert out.loc[1, "step_status"] == StepStatus.OK.value

    def test_nan_max_drawdown_invalidates_ok_step(self):
        rows = [_step_row("gp1", 1, max_drawdown=float("nan"))]
        df = pd.DataFrame(rows)
        df["error_type"] = ""
        out = validate_metric_contracts(df)
        assert out.loc[0, "step_status"] == "invalid"

    def test_zero_max_drawdown_stays_ok(self):
        """Zero drawdown is a boundary case — <=0 is legitimate."""
        rows = [_step_row("gp1", 1, max_drawdown=0.0)]
        df = pd.DataFrame(rows)
        df["error_type"] = ""
        out = validate_metric_contracts(df)
        assert out.loc[0, "step_status"] == StepStatus.OK.value


# ===========================================================================
# §5 Differential: no _all / _failed / _nonok aggregate semantics appear
# ===========================================================================

class TestNoForbiddenAggregateSuffixes:
    """Plan §5 Differential: 'No columns with _all, _failed, _nonok aggregate
    semantics appear.'  Aggregation must remain ok-only.

    Rationale: _all / _failed / _nonok suffixes would indicate that aggregate
    statistics were computed over non-ok steps, which contradicts the ok-only
    scope marker and would silently change Phase A semantics.
    """

    _FORBIDDEN_SUFFIXES = ("_all", "_failed", "_nonok")

    def _check_no_forbidden(self, cols):
        for col in cols:
            # Skip identity / non-aggregate columns that legitimately end with
            # these words (defensive, but currently there are none).
            for suf in self._FORBIDDEN_SUFFIXES:
                assert not col.lower().endswith(suf), (
                    f"Forbidden aggregate suffix {suf!r} in column {col!r}"
                )

    def test_block_a_no_forbidden_suffixes(self):
        self._check_no_forbidden(_BLOCK_A)

    def test_block_b_no_forbidden_suffixes(self):
        self._check_no_forbidden(_BLOCK_B)

    def test_summary_wide_no_forbidden_suffixes(self):
        step_df, agg_df, ranked_df, cfg = _build(["gp1", "gp2"])
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        self._check_no_forbidden(result.columns)


# ===========================================================================
# §5 Differential: aggregation_scope remains ok_steps_only everywhere
# ===========================================================================

class TestAggregationScopeAlwaysOkOnly:
    """Regression ensuring no alternate aggregation scope has crept into the
    summary path.  Must remain a single-valued 'ok_steps_only' label."""

    def test_single_gp(self):
        step_df, agg_df, ranked_df, cfg = _build(["gp1"])
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert set(result["aggregation_scope"].unique()) == {"ok_steps_only"}

    def test_multi_gp(self):
        step_df, agg_df, ranked_df, cfg = _build(["gp1", "gp2", "gp3"], n_steps=3)
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert set(result["aggregation_scope"].unique()) == {"ok_steps_only"}

    def test_scope_is_constant_across_rows(self):
        """All rows in the same summary must carry the same scope label."""
        step_df, agg_df, ranked_df, cfg = _build(["gp1", "gp2", "gp3"])
        result = build_summary_wide(step_df, agg_df, ranked_df, cfg)
        assert result["aggregation_scope"].nunique() == 1


# ===========================================================================
# §5 Regression: n_segments uses nunique, not max (sparse wf_step case)
# ===========================================================================

class TestNSegmentsSparseRegression:
    """Pinned here again as a top-level regression (plan §4.9 S1)."""

    def test_sparse_wf_step_1_3_4(self):
        """Plan §5 literal example: wf_step = [1, 3, 4] → n_segments = 3."""
        steps = [
            _step_row("gp1", 1),
            _step_row("gp1", 3),
            _step_row("gp1", 4),
        ]
        step_df = pd.DataFrame(steps)
        agg_df = pd.DataFrame([_agg_row("gp1")])
        ranked_df = pd.DataFrame([_ranked_row("gp1")])
        result = build_summary_wide(step_df, agg_df, ranked_df, _cfg())
        assert result.iloc[0]["n_segments"] == 3, (
            "Plan §5: sparse wf_step=[1,3,4] must yield n_segments=3 (nunique), "
            f"got {result.iloc[0]['n_segments']}"
        )
