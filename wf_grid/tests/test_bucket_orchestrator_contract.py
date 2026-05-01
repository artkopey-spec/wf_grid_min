"""
Contract tests: BucketMatrix_Median integration in run_grid_pipeline (audit A/B).

- B1: builder exception does not abort the pipeline.
- B2: builder receives pre-gate step_oos_long (no gate_failed); post-gate may have gate_failed.
- B3: builder input row count = len(grid_points) * n_wf_steps (full long, not shortlist).
"""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from wf_grid.pipeline.orchestrator import run_grid_pipeline
from wf_grid.status.status_model import StepStatus


def _write_synthetic_ohlc_csv(
    path: Path,
    n_bars: int = 800,
    *,
    volatility_amplitude: float = 0.0,
) -> None:
    """Write synthetic OHLC CSV for contract tests.

    ``volatility_amplitude`` > 0 adds a sine-wave oscillation on top of the
    linear trend so that ok-steps produce downside periods and therefore
    finite sortino values.  The monotone default (amplitude=0) is retained
    for tests that only care about pipeline plumbing, while tests that need
    genuine ok-steps to reach step_gates after the metric-contract validator
    (§4.3 of the fix plan) pass a non-zero amplitude.
    """
    idx = pd.date_range("2020-01-01", periods=n_bars, freq="1D")
    base = np.linspace(100.0, 200.0, n_bars)
    if volatility_amplitude > 0.0:
        base = base + volatility_amplitude * np.sin(np.arange(n_bars) / 10.0)
    df = pd.DataFrame(
        {
            "open": base,
            "high": base + 1.0,
            "low": base - 1.0,
            "close": base + 0.5,
        },
        index=idx,
    )
    df.to_csv(path)


def _yaml_for_contract_tests(
    csv_path: Path,
    *,
    step_min_trades: int | None = None,
    backtest_min_trades_required: int = 3,
) -> str:
    """Minimal valid grid (1 ATR × 1 mult) + WF windows that fit ~800 daily bars.

    ``backtest_min_trades_required`` controls the step_executor's trade-level
    override threshold: steps with num_trades below it receive
    INVALID_METRIC_VALUE in sharpe/sortino/cagr.  Contract tests that need
    genuine ok-steps to reach step_gates (e.g. the post-gate gate_failed
    assertion) must lower this to 1 so the new metric-contract validator
    (§4.3 of the fix plan) does not invalidate those steps upstream.
    """
    smt = "null" if step_min_trades is None else step_min_trades
    p = str(csv_path).replace("\\", "/")
    return textwrap.dedent(
        f"""\
        data:
          file_path: "{p}"
          periods_per_year: 252

        optimization:
          atr_period_range: [14, 14]
          multiplier_range: [2.0, 2.0]
          multiplier_step: 0.1
          trade_mode: "long"

        backtest:
          commission: 0.000235
          min_trades_required: {backtest_min_trades_required}
          early_exit_enabled: false
          early_exit_max_drawdown: 0.50
          early_exit_check_bars: 50

        validation:
          warmup_period: 0
          warmup_period_auto: false
          walk_forward:
            train_size: "200bars"
            test_size: "100bars"
            step_size: "100bars"
            scheme: "rolling"
            anchor: "start"
            min_train_bars: 150
            min_test_bars: 50

        gates:
          step:
            min_trades: {smt}
            max_drawdown_threshold: -0.50
          candidate:
            positive_median_threshold: 0.0
            min_trades_median: 3.0
            worst_segment_pnl_threshold: null
            max_drawdown_threshold: -0.50
            min_ok_ratio: 0.1
            min_total_trades: 1

        ranking:
          mode: "gates_score"
          min_segments_for_ranking: null
          sort_by: "sum_pnl_pct_Median"
          tiebreaker: "sum_pnl_pct_Min"

        scoring:
          normalization_mode: "minmax"
          score_weights:
            sum_pnl_pct_Median: 0.45
            profitable_segments_count: 0.35
            abs_max_drawdown_Min: 0.20

        status:
          min_meaningful_bars: 30
        """
    )


@pytest.fixture
def contract_run_files(tmp_path: Path) -> tuple[Path, Path]:
    csv_path = tmp_path / "ohlc.csv"
    _write_synthetic_ohlc_csv(csv_path, n_bars=800)
    yaml_path = tmp_path / "cfg.yaml"
    yaml_path.write_text(_yaml_for_contract_tests(csv_path), encoding="utf-8")
    return yaml_path, tmp_path / "out.xlsx"


@pytest.fixture
def contract_run_files_strict_step_gate(tmp_path: Path) -> tuple[Path, Path]:
    """Same as contract_run_files but step min_trades set high to force gate_failed post-gate.

    Two fixture tweaks are needed so that ok-steps survive the metric-contract
    validator (§4.3) and actually reach apply_step_gates:

    * ``backtest_min_trades_required=1`` — avoids step_executor writing
      INVALID_METRIC_VALUE into sharpe/sortino/cagr for single-trade steps.
    * ``volatility_amplitude=5.0`` on the synthetic OHLC — introduces downside
      periods so sortino is finite (pure monotone trend yields sortino=-999).

    Together these keep ok-step soft metrics finite, so the validator leaves
    them alone and the subsequent step gate (min_trades=9999) converts ok
    -> gate_failed as the test expects.
    """
    csv_path = tmp_path / "ohlc.csv"
    _write_synthetic_ohlc_csv(csv_path, n_bars=800, volatility_amplitude=5.0)
    yaml_path = tmp_path / "cfg.yaml"
    yaml_path.write_text(
        _yaml_for_contract_tests(
            csv_path,
            step_min_trades=9999,
            backtest_min_trades_required=1,
        ),
        encoding="utf-8",
    )
    return yaml_path, tmp_path / "out.xlsx"


class TestBucketBuilderDoesNotAbortPipeline:
    """B1: isolated try/except — builder failure must not set result.error."""

    def test_builder_exception_pipeline_completes(
        self,
        contract_run_files: tuple[Path, Path],
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        import wf_grid.bucket.median_matrix_builder as mmb

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated bucket matrix failure")

        monkeypatch.setattr(mmb, "build_median_bucket_matrix", _boom)

        yaml_path, out_path = contract_run_files
        with caplog.at_level(logging.WARNING, logger="wf_grid.pipeline.orchestrator"):
            result = run_grid_pipeline(str(yaml_path), output_path=str(out_path), parallel_enabled=False)

        assert result.error is None, f"pipeline should not fail: {result.error}"
        assert result.bucket_matrix_median is None
        assert result.ranked is not None
        assert result.summary_wide is not None
        assert result.step_oos_long is not None
        assert any(
            "Bucket matrix build failed" in r.getMessage()
            for r in caplog.records
            if r.levelno == logging.WARNING
        )


class TestBucketBuilderPreGateInput:
    """B2 + B3: capture builder input from a real orchestrator run."""

    def test_builder_receives_no_gate_failed_status(
        self,
        contract_run_files_strict_step_gate: tuple[Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import wf_grid.bucket.median_matrix_builder as mmb

        captured: list[pd.DataFrame] = []
        real = mmb.build_median_bucket_matrix

        def _wrap(df: pd.DataFrame, config, **kwargs):
            captured.append(df)
            return real(df, config, **kwargs)

        monkeypatch.setattr(mmb, "build_median_bucket_matrix", _wrap)

        yaml_path, out_path = contract_run_files_strict_step_gate
        result = run_grid_pipeline(str(yaml_path), output_path=str(out_path), parallel_enabled=False)

        assert result.error is None
        assert len(captured) == 1
        pre = captured[0]
        assert "step_status" in pre.columns
        assert not (pre["step_status"] == StepStatus.GATE_FAILED.value).any(), (
            "pre-gate step_oos_long must not contain gate_failed before apply_step_gates"
        )

        post = result.step_oos_long
        assert post is not None
        assert (post["step_status"] == StepStatus.GATE_FAILED.value).any(), (
            "expected at least one gate_failed after step gates with min_trades=9999"
        )

    def test_builder_input_rows_equal_full_grid_long(
        self,
        contract_run_files: tuple[Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """B3: input long size = |grid_points| * n_wf_steps (regression vs shortlist)."""
        import wf_grid.bucket.median_matrix_builder as mmb

        captured: list[pd.DataFrame] = []
        real = mmb.build_median_bucket_matrix

        def _wrap(df: pd.DataFrame, config, **kwargs):
            captured.append(df)
            return real(df, config, **kwargs)

        monkeypatch.setattr(mmb, "build_median_bucket_matrix", _wrap)

        yaml_path, out_path = contract_run_files
        result = run_grid_pipeline(str(yaml_path), output_path=str(out_path), parallel_enabled=False)

        assert result.error is None
        assert len(captured) == 1
        assert result.diagnostics is not None
        n_gp = len(result.grid_points)
        n_wf = result.diagnostics.n_wf_steps
        assert n_gp >= 1 and n_wf >= 1
        assert len(captured[0]) == n_gp * n_wf
