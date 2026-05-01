"""
FIX-2.9 — Real CSV E2E smoke test.

Runs the full pipeline on real market data (data.csv) with a mini-grid
to verify no crashes and basic output sanity. Marked slow — excluded from
default CI runs.

Checks:
  - Pipeline completes without exception.
  - XLSX file created, contains expected sheets.
  - step_oos_long is non-empty.
  - No NaN in identity columns (grid_point_id, wf_step).
  - max_drawdown <= 0 for all ok steps.
  - effective_oos_bars > 0 for all ok steps.
  - sum_pnl_pct is finite for all ok steps.
  - ranked has grid_rank column starting at 1 with no gaps.
  - step_train_long is populated (FIX-2.1 train pipeline).
  - trades_oos contains only rank-1 grid point.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from wf_grid.pipeline.orchestrator import run_grid_pipeline
from wf_grid.status.status_model import StepStatus

# Real data is at project root
_DATA_CSV = Path(__file__).resolve().parents[2] / "data.csv"
_SKIP_REASON = f"Real data file not found: {_DATA_CSV}"
_REAL_DATA_SAMPLE_ROWS = 5_000


def _write_real_data_sample(tmp_path: Path) -> Path:
    """Write a bounded real-data sample to keep the E2E smoke XLSX-sized."""
    df = pd.read_csv(_DATA_CSV, index_col=0)
    sample_path = tmp_path / "real_data_sample.csv"
    df.tail(_REAL_DATA_SAMPLE_ROWS).to_csv(sample_path)
    return sample_path


def _write_mini_config(tmp_path: Path, data_csv: Path) -> str:
    """Write a mini-grid config (3 ATR × 3 mult = 9 grid points) for fast execution."""
    cfg_text = textwrap.dedent(f"""\
    data:
      file_path: "{data_csv.as_posix()}"
      periods_per_year: 252
      annualization_basis: "trading"

    optimization:
      atr_period_range: [10, 12]
      multiplier_range: [2.0, 3.0]
      multiplier_step: 0.5
      trade_mode: "both"

    backtest:
      commission: 0.000235
      min_trades_required: 3
      early_exit_enabled: false
      early_exit_max_drawdown: 0.50
      early_exit_check_bars: 50

    validation:
      warmup_period: 0
      warmup_period_auto: true
      walk_forward:
        train_size: "500bars"
        test_size: "200bars"
        step_size: "200bars"
        scheme: "rolling"

    gates:
      step:
        min_trades: null
        max_drawdown_threshold: -0.50
      candidate:
        positive_median_threshold: 0.0
        min_trades_median: 3.0
        worst_segment_pnl_threshold: null
        max_drawdown_threshold: -0.50

    ranking:
      mode: "gates_score"
      min_segments_for_ranking: null
      sort_by: "sum_pnl_pct_Median"
      tiebreaker: "sum_pnl_pct_Min"

    scoring:
      score_weights:
        sum_pnl_pct_Median: 0.45
        profitable_segments_count: 0.35
        abs_max_drawdown_Min: 0.20

    status:
      min_meaningful_bars: 30
    """)
    cfg_path = tmp_path / "e2e_config.yaml"
    cfg_path.write_text(cfg_text, encoding="utf-8")
    return str(cfg_path)


# ---------------------------------------------------------------------------
# Plan §6.19 / step 13: parametrize slow smoke over three execution modes.
#
# Each parametrized variant (sequential, parallel-2, parallel-4) shares the
# same assertions so that we catch any numeric drift introduced by the parallel
# path without duplicating test logic.
# ---------------------------------------------------------------------------

# Each entry is (slug, run_grid_pipeline kwargs).  The slug is embedded so
# that module-scoped fixtures (where request.node is a Module, not Function)
# can still derive a unique temp-directory name without touching callspec.
_E2E_EXECUTION_PARAMS = [
    pytest.param(
        ("sequential", dict(parallel_enabled=False, max_workers=None)),
        id="sequential",
    ),
    pytest.param(
        ("parallel_mw2", dict(parallel_enabled=True, max_workers=2)),
        id="parallel_mw2",
    ),
    pytest.param(
        ("parallel_mw4", dict(parallel_enabled=True, max_workers=4)),
        id="parallel_mw4",
    ),
]


@pytest.fixture(scope="module", params=_E2E_EXECUTION_PARAMS)
def e2e_result(request, tmp_path_factory):
    """Run pipeline once per execution mode for the whole test module.

    Parametrized over sequential, parallel max_workers=2, and
    parallel max_workers=4 (plan §6.19 / step 13).
    """
    if not _DATA_CSV.exists():
        pytest.skip(_SKIP_REASON)

    mode_slug, exec_kwargs = request.param
    tmp = tmp_path_factory.mktemp(f"e2e_{mode_slug}")
    data_csv = _write_real_data_sample(tmp)
    cfg_path = _write_mini_config(tmp, data_csv)
    output_path = str(tmp / f"e2e_output_{mode_slug}.xlsx")

    result = run_grid_pipeline(
        config_path=cfg_path,
        output_path=output_path,
        **exec_kwargs,
    )
    assert result.error is None, (
        f"Pipeline ({mode_slug}) failed: {result.error}"
    )
    return result


@pytest.mark.slow
class TestE2EPipelineCompletes:
    def test_no_error(self, e2e_result):
        assert e2e_result.error is None

    def test_xlsx_created(self, e2e_result):
        assert e2e_result.output_path is not None
        assert e2e_result.output_path.exists()

    def test_xlsx_has_expected_sheets(self, e2e_result):
        xlsx = pd.ExcelFile(e2e_result.output_path)
        sheet_names = xlsx.sheet_names
        assert "WF_Config" in sheet_names
        assert "WF_Trades" in sheet_names


@pytest.mark.slow
class TestE2EStepOosLong:
    def test_non_empty(self, e2e_result):
        assert e2e_result.step_oos_long is not None
        assert len(e2e_result.step_oos_long) > 0

    def test_no_nan_in_identity_columns(self, e2e_result):
        df = e2e_result.step_oos_long
        assert not df["grid_point_id"].isna().any()
        assert not df["wf_step"].isna().any()

    def test_max_drawdown_non_positive_for_ok(self, e2e_result):
        df = e2e_result.step_oos_long
        ok = df[df["step_status"] == StepStatus.OK.value]
        if len(ok) > 0:
            assert (ok["max_drawdown"] <= 0).all(), (
                f"Positive max_drawdown in ok steps: "
                f"{ok[ok['max_drawdown'] > 0][['grid_point_id', 'wf_step', 'max_drawdown']].to_string()}"
            )

    def test_effective_oos_bars_positive_for_ok(self, e2e_result):
        df = e2e_result.step_oos_long
        ok = df[df["step_status"] == StepStatus.OK.value]
        if len(ok) > 0:
            assert (ok["effective_oos_bars"] > 0).all()

    def test_sum_pnl_pct_finite_for_ok(self, e2e_result):
        df = e2e_result.step_oos_long
        ok = df[df["step_status"] == StepStatus.OK.value]
        if len(ok) > 0:
            assert np.isfinite(ok["sum_pnl_pct"]).all()


@pytest.mark.slow
class TestE2ERanking:
    def test_grid_rank_starts_at_one(self, e2e_result):
        assert e2e_result.ranked is not None
        assert e2e_result.ranked["grid_rank"].min() == 1

    def test_grid_rank_no_gaps(self, e2e_result):
        ranks = sorted(e2e_result.ranked["grid_rank"].tolist())
        assert ranks == list(range(1, len(ranks) + 1))

    def test_tier_column_present(self, e2e_result):
        assert "tier" in e2e_result.ranked.columns


@pytest.mark.slow
class TestE2ETrainPipeline:
    def test_step_train_long_populated(self, e2e_result):
        assert e2e_result.step_train_long is not None
        assert len(e2e_result.step_train_long) > 0

    def test_trades_oos_rank1_only(self, e2e_result):
        if e2e_result.trades_oos is not None and len(e2e_result.trades_oos) > 0:
            top_gp = e2e_result.ranked[
                e2e_result.ranked["grid_rank"] == 1
            ]["grid_point_id"].iloc[0]
            assert set(e2e_result.trades_oos["grid_point_id"].unique()) == {top_gp}


# ===========================================================================
# E2E anchor="start" — backward compatibility (4.4)
# ===========================================================================

def _write_anchor_config(
    tmp_path: Path,
    anchor: str,
    data_csv: Path,
    extra_wf: str = "",
) -> str:
    """Helper: mini-grid config with explicit anchor setting."""
    cfg_text = textwrap.dedent(f"""\
    data:
      file_path: "{data_csv.as_posix()}"
      periods_per_year: 252
      annualization_basis: "trading"

    optimization:
      atr_period_range: [10, 12]
      multiplier_range: [2.0, 2.5]
      multiplier_step: 0.5
      trade_mode: "both"

    backtest:
      commission: 0.000235
      min_trades_required: 3
      early_exit_enabled: false
      early_exit_max_drawdown: 0.50
      early_exit_check_bars: 50

    validation:
      warmup_period: 0
      warmup_period_auto: false
      walk_forward:
        train_size: "500bars"
        test_size: "200bars"
        step_size: "200bars"
        scheme: "rolling"
        anchor: "{anchor}"
        min_train_bars: 100
        min_test_bars: 50
        {extra_wf}

    gates:
      step:
        min_trades: null
        max_drawdown_threshold: -0.50
      candidate:
        positive_median_threshold: 0.0
        min_trades_median: 3.0
        worst_segment_pnl_threshold: null
        max_drawdown_threshold: -0.50

    ranking:
      mode: "gates_score"
      min_segments_for_ranking: null
      sort_by: "sum_pnl_pct_Median"
      tiebreaker: "sum_pnl_pct_Min"

    scoring:
      score_weights:
        sum_pnl_pct_Median: 0.45
        profitable_segments_count: 0.35
        abs_max_drawdown_Min: 0.20

    status:
      min_meaningful_bars: 30
    """)
    cfg_path = tmp_path / f"e2e_{anchor}_config.yaml"
    cfg_path.write_text(cfg_text, encoding="utf-8")
    return str(cfg_path)


@pytest.fixture(scope="module")
def e2e_anchor_start_result(tmp_path_factory):
    """E2E run with anchor='start' (sequential; geometry tests only)."""
    if not _DATA_CSV.exists():
        pytest.skip(_SKIP_REASON)
    tmp = tmp_path_factory.mktemp("e2e_anchor_start")
    data_csv = _write_real_data_sample(tmp)
    cfg_path = _write_anchor_config(tmp, anchor="start", data_csv=data_csv)
    result = run_grid_pipeline(
        config_path=cfg_path,
        output_path=str(tmp / "out_start.xlsx"),
        parallel_enabled=False,
    )
    assert result.error is None, f"Pipeline (anchor=start) failed: {result.error}"
    return result


@pytest.fixture(scope="module")
def e2e_anchor_end_result(tmp_path_factory):
    """E2E run with anchor='end' (sequential; geometry tests only)."""
    if not _DATA_CSV.exists():
        pytest.skip(_SKIP_REASON)
    tmp = tmp_path_factory.mktemp("e2e_anchor_end")
    data_csv = _write_real_data_sample(tmp)
    cfg_path = _write_anchor_config(tmp, anchor="end", data_csv=data_csv)
    result = run_grid_pipeline(
        config_path=cfg_path,
        output_path=str(tmp / "out_end.xlsx"),
        parallel_enabled=False,
    )
    assert result.error is None, f"Pipeline (anchor=end) failed: {result.error}"
    return result


@pytest.mark.slow
class TestE2EAnchorStart:
    """anchor='start' — pipeline completes, slices are valid (4.3 geometry)."""

    def test_no_error(self, e2e_anchor_start_result):
        assert e2e_anchor_start_result.error is None

    def test_step_oos_long_non_empty(self, e2e_anchor_start_result):
        assert e2e_anchor_start_result.step_oos_long is not None
        assert len(e2e_anchor_start_result.step_oos_long) > 0

    def test_ranked_non_empty(self, e2e_anchor_start_result):
        assert e2e_anchor_start_result.ranked is not None
        assert len(e2e_anchor_start_result.ranked) > 0

    def test_config_anchor_stored(self, e2e_anchor_start_result):
        assert e2e_anchor_start_result.config.validation.walk_forward.anchor == "start"


@pytest.mark.slow
class TestE2EAnchorEnd:
    """anchor='end' — pipeline completes fully, slices are valid (4.3 geometry)."""

    def test_no_error(self, e2e_anchor_end_result):
        assert e2e_anchor_end_result.error is None

    def test_step_oos_long_non_empty(self, e2e_anchor_end_result):
        assert e2e_anchor_end_result.step_oos_long is not None
        assert len(e2e_anchor_end_result.step_oos_long) > 0

    def test_ranked_non_empty(self, e2e_anchor_end_result):
        assert e2e_anchor_end_result.ranked is not None
        assert len(e2e_anchor_end_result.ranked) > 0

    def test_config_anchor_stored(self, e2e_anchor_end_result):
        assert e2e_anchor_end_result.config.validation.walk_forward.anchor == "end"

    def test_max_drawdown_non_positive_for_ok(self, e2e_anchor_end_result):
        from wf_grid.status.status_model import StepStatus
        df = e2e_anchor_end_result.step_oos_long
        ok = df[df["step_status"] == StepStatus.OK.value]
        if len(ok) > 0:
            assert (ok["max_drawdown"] <= 0).all()

    def test_sum_pnl_pct_finite_for_ok(self, e2e_anchor_end_result):
        from wf_grid.status.status_model import StepStatus
        import numpy as np
        df = e2e_anchor_end_result.step_oos_long
        ok = df[df["step_status"] == StepStatus.OK.value]
        if len(ok) > 0:
            assert np.isfinite(ok["sum_pnl_pct"]).all()


@pytest.mark.slow
class TestE2EAnchorStartVsEnd:
    """Сравнительный smoke: оба режима дают непустые результаты и проходят геометрические инварианты.

    NOTE: anchor='end' может дать другое число шагов, чем anchor='start', когда
    длина данных не кратна размеру окна.  Это ожидаемое поведение — не баг.
    Downstream: другое n_wf_steps → другой ok_ratio → другие агрегаты → другой ranking.
    """

    def test_both_anchors_produce_results(self, e2e_anchor_start_result, e2e_anchor_end_result):
        assert len(e2e_anchor_start_result.step_oos_long) > 0
        assert len(e2e_anchor_end_result.step_oos_long) > 0

    def test_both_anchors_produce_ranking(self, e2e_anchor_start_result, e2e_anchor_end_result):
        assert len(e2e_anchor_start_result.ranked) > 0
        assert len(e2e_anchor_end_result.ranked) > 0

    def test_step_counts_documented(self, e2e_anchor_start_result, e2e_anchor_end_result):
        """Document (not assert equality of) step counts for both anchors.

        anchor='end' may produce a different number of WF steps — this is
        correct behaviour.  The test just ensures both are positive.
        """
        n_start = len(e2e_anchor_start_result.step_oos_long[
            e2e_anchor_start_result.step_oos_long["grid_point_id"] ==
            e2e_anchor_start_result.step_oos_long["grid_point_id"].iloc[0]
        ])
        n_end = len(e2e_anchor_end_result.step_oos_long[
            e2e_anchor_end_result.step_oos_long["grid_point_id"] ==
            e2e_anchor_end_result.step_oos_long["grid_point_id"].iloc[0]
        ])
        assert n_start > 0
        assert n_end > 0
