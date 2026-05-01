"""
Tests for A8: Gates (step + candidate + composite)

Coverage:
  Step-level gates (§6.1):
    - min_trades gate: ok rows below threshold → gate_failed
    - max_drawdown gate: ok rows below threshold → gate_failed
    - non-ok rows are untouched by step gates
    - both gates can fail simultaneously
    - gate values at exact boundary → pass
    - original DataFrame not mutated (copy semantics)

  Candidate-level gates (§6.2):
    - gate_ok_positive_median: strictly greater than threshold
    - gate_ok_min_trades: >= threshold
    - gate_ok_drawdown: >= threshold (negative values)
    - gate_ok_worst_segment: disabled when None, enabled with threshold
    - n_ok_steps == 0 → all gates False, seed_gate_passed False

  Composite (§6.3):
    - seed_gate_passed = AND of all gates
    - seed_gate_fail_reason: comma-separated failed gates, empty when all pass
    - no_ok_steps special fail reason
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import pytest

from wf_grid.config.schema import (
    BacktestConfig,
    CandidateGatesConfig,
    DataConfig,
    GatesConfig,
    GridConfig,
    StepGatesConfig,
)
from wf_grid.gates.gates import (
    _CANDIDATE_GATE_COLUMNS,
    apply_candidate_gates,
    apply_step_gates,
)
from wf_grid.status.status_model import StepStatus


# ---------------------------------------------------------------------------
# Helpers — minimal config
# ---------------------------------------------------------------------------

def _cfg(
    step_min_trades=None,
    step_max_dd=-0.50,
    cand_positive_median=0.0,
    cand_min_trades_median=3.0,
    cand_worst_segment=None,
    cand_max_dd=-0.50,
    cand_min_total_trades=30,
    cand_min_ok_ratio=0.7,
    backtest_min_trades=3,
) -> GridConfig:
    return GridConfig(
        data=DataConfig(file_path="dummy.csv"),
        backtest=BacktestConfig(min_trades_required=backtest_min_trades),
        gates=GatesConfig(
            step=StepGatesConfig(
                min_trades=step_min_trades,
                max_drawdown_threshold=step_max_dd,
            ),
            candidate=CandidateGatesConfig(
                positive_median_threshold=cand_positive_median,
                min_trades_median=cand_min_trades_median,
                worst_segment_pnl_threshold=cand_worst_segment,
                max_drawdown_threshold=cand_max_dd,
                min_total_trades=cand_min_total_trades,
                min_ok_ratio=cand_min_ok_ratio,
            ),
        ),
    )


def _step_row(
    gp_id: str = "gp1",
    wf_step: int = 1,
    status: str = StepStatus.OK.value,
    num_trades: int = 10,
    max_drawdown: float = -0.10,
) -> dict:
    return {
        "grid_point_id": gp_id,
        "wf_step": wf_step,
        "step_status": status,
        "num_trades": num_trades,
        "max_drawdown": max_drawdown,
        "sum_pnl_pct": 5.0,
        "sharpe": 1.0,
        "sortino": 1.0,
        "cagr": 0.1,
        "win_rate": 0.6,
        "profit_factor": 1.5,
        "avg_trade": 0.5,
    }


def _step_df(*rows) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


def _agg_row(
    gp_id: str = "gp1",
    n_ok: int = 3,
    n_total: int = 3,
    pnl_median: float = 5.0,
    trades_median: float = 10.0,
    pnl_min: float = 1.0,
    dd_min: float = -0.20,
    total_oos_trades: int = 50,
) -> dict:
    return {
        "grid_point_id": gp_id,
        "n_ok_steps": n_ok,
        "n_total_steps": n_total,
        "ok_ratio": n_ok / n_total if n_total else 0.0,
        "sum_pnl_pct_Median": pnl_median,
        "num_trades_Median": trades_median,
        "sum_pnl_pct_Min": pnl_min,
        "max_drawdown_Min": dd_min,
        "total_oos_trades": total_oos_trades,
    }


def _agg_df(*rows) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


# ===========================================================================
# Step-level gates (§6.1)
# ===========================================================================

class TestStepGateMinTrades:
    def test_below_threshold_fails(self):
        df = _step_df(_step_row(num_trades=2))
        config = _cfg(backtest_min_trades=3)
        result = apply_step_gates(df, config)
        assert result.iloc[0]["step_status"] == StepStatus.GATE_FAILED.value

    def test_at_threshold_passes(self):
        df = _step_df(_step_row(num_trades=3))
        config = _cfg(backtest_min_trades=3)
        result = apply_step_gates(df, config)
        assert result.iloc[0]["step_status"] == StepStatus.OK.value

    def test_above_threshold_passes(self):
        df = _step_df(_step_row(num_trades=10))
        config = _cfg(backtest_min_trades=3)
        result = apply_step_gates(df, config)
        assert result.iloc[0]["step_status"] == StepStatus.OK.value

    def test_explicit_step_min_trades_overrides_backtest(self):
        df = _step_df(_step_row(num_trades=4))
        config = _cfg(step_min_trades=5, backtest_min_trades=3)
        result = apply_step_gates(df, config)
        assert result.iloc[0]["step_status"] == StepStatus.GATE_FAILED.value


class TestStepGateMaxDrawdown:
    def test_below_threshold_fails(self):
        df = _step_df(_step_row(max_drawdown=-0.60))
        config = _cfg(step_max_dd=-0.50)
        result = apply_step_gates(df, config)
        assert result.iloc[0]["step_status"] == StepStatus.GATE_FAILED.value

    def test_at_threshold_passes(self):
        df = _step_df(_step_row(max_drawdown=-0.50))
        config = _cfg(step_max_dd=-0.50)
        result = apply_step_gates(df, config)
        assert result.iloc[0]["step_status"] == StepStatus.OK.value

    def test_above_threshold_passes(self):
        df = _step_df(_step_row(max_drawdown=-0.10))
        config = _cfg(step_max_dd=-0.50)
        result = apply_step_gates(df, config)
        assert result.iloc[0]["step_status"] == StepStatus.OK.value


class TestStepGateNonOkUntouched:
    def test_non_ok_rows_not_affected(self):
        df = _step_df(
            _step_row(wf_step=1, status=StepStatus.NO_TRADES.value, num_trades=0, max_drawdown=-0.90),
            _step_row(wf_step=2, status=StepStatus.INSUFFICIENT_BARS.value, num_trades=1, max_drawdown=-0.80),
            _step_row(wf_step=3, status=StepStatus.RUNTIME_ERROR.value, num_trades=0, max_drawdown=-0.99),
        )
        config = _cfg()
        result = apply_step_gates(df, config)
        assert result.iloc[0]["step_status"] == StepStatus.NO_TRADES.value
        assert result.iloc[1]["step_status"] == StepStatus.INSUFFICIENT_BARS.value
        assert result.iloc[2]["step_status"] == StepStatus.RUNTIME_ERROR.value


class TestStepGateBothFail:
    def test_both_gates_fail_simultaneously(self):
        df = _step_df(_step_row(num_trades=1, max_drawdown=-0.80))
        config = _cfg(backtest_min_trades=3, step_max_dd=-0.50)
        result = apply_step_gates(df, config)
        assert result.iloc[0]["step_status"] == StepStatus.GATE_FAILED.value


class TestStepGateCopySemantics:
    def test_original_not_mutated(self):
        df = _step_df(_step_row(num_trades=1))
        config = _cfg(backtest_min_trades=3)
        _ = apply_step_gates(df, config)
        assert df.iloc[0]["step_status"] == StepStatus.OK.value


# ===========================================================================
# FIX-1.4 — NaN in step-level gate metrics must fail the gate
# ===========================================================================

class TestStepGateNaNValues:
    """FIX-1.4: NaN in gate metric → gate_failed (unknown quality must not pass)."""

    def test_nan_max_drawdown_fails_gate(self):
        row = _step_row(num_trades=10, max_drawdown=-0.10)
        row["max_drawdown"] = float("nan")
        df = _step_df(row)
        config = _cfg(backtest_min_trades=3, step_max_dd=-0.50)
        result = apply_step_gates(df, config)
        assert result.iloc[0]["step_status"] == StepStatus.GATE_FAILED.value

    def test_nan_num_trades_fails_gate(self):
        row = _step_row(num_trades=10, max_drawdown=-0.10)
        row["num_trades"] = float("nan")
        df = _step_df(row)
        config = _cfg(backtest_min_trades=3, step_max_dd=-0.50)
        result = apply_step_gates(df, config)
        assert result.iloc[0]["step_status"] == StepStatus.GATE_FAILED.value

    def test_both_nan_fails_gate(self):
        row = _step_row(num_trades=10, max_drawdown=-0.10)
        row["num_trades"] = float("nan")
        row["max_drawdown"] = float("nan")
        df = _step_df(row)
        config = _cfg(backtest_min_trades=3, step_max_dd=-0.50)
        result = apply_step_gates(df, config)
        assert result.iloc[0]["step_status"] == StepStatus.GATE_FAILED.value

    def test_non_nan_passing_values_still_ok(self):
        """Regression: valid non-NaN values that pass thresholds must stay ok."""
        df = _step_df(_step_row(num_trades=10, max_drawdown=-0.10))
        config = _cfg(backtest_min_trades=3, step_max_dd=-0.50)
        result = apply_step_gates(df, config)
        assert result.iloc[0]["step_status"] == StepStatus.OK.value

    def test_nan_only_in_ok_rows_non_ok_untouched(self):
        """NaN-triggered gate failure applies only to ok rows; non-ok rows unchanged."""
        ok_row = _step_row(wf_step=1, status=StepStatus.OK.value, num_trades=10)
        ok_row["max_drawdown"] = float("nan")
        non_ok_row = _step_row(wf_step=2, status=StepStatus.NO_TRADES.value, num_trades=0)
        non_ok_row["max_drawdown"] = float("nan")
        df = _step_df(ok_row, non_ok_row)
        config = _cfg()
        result = apply_step_gates(df, config)
        assert result.iloc[0]["step_status"] == StepStatus.GATE_FAILED.value
        assert result.iloc[1]["step_status"] == StepStatus.NO_TRADES.value


# ===========================================================================
# Candidate-level gates (§6.2)
# ===========================================================================

class TestCandidateGatePositiveMedian:
    def test_positive_passes(self):
        df = _agg_df(_agg_row(pnl_median=1.0))
        result = apply_candidate_gates(df, _cfg())
        assert bool(result.iloc[0]["gate_ok_positive_median"]) is True

    def test_zero_fails(self):
        # strictly greater than, so 0.0 > 0.0 → False
        df = _agg_df(_agg_row(pnl_median=0.0))
        result = apply_candidate_gates(df, _cfg(cand_positive_median=0.0))
        assert bool(result.iloc[0]["gate_ok_positive_median"]) is False

    def test_negative_fails(self):
        df = _agg_df(_agg_row(pnl_median=-1.0))
        result = apply_candidate_gates(df, _cfg())
        assert bool(result.iloc[0]["gate_ok_positive_median"]) is False


class TestCandidateGateMinTrades:
    def test_at_threshold_passes(self):
        df = _agg_df(_agg_row(trades_median=3.0))
        result = apply_candidate_gates(df, _cfg(cand_min_trades_median=3.0))
        assert bool(result.iloc[0]["gate_ok_min_trades"]) is True

    def test_below_threshold_fails(self):
        df = _agg_df(_agg_row(trades_median=2.0))
        result = apply_candidate_gates(df, _cfg(cand_min_trades_median=3.0))
        assert bool(result.iloc[0]["gate_ok_min_trades"]) is False


class TestCandidateGateDrawdown:
    def test_within_threshold_passes(self):
        df = _agg_df(_agg_row(dd_min=-0.30))
        result = apply_candidate_gates(df, _cfg(cand_max_dd=-0.50))
        assert bool(result.iloc[0]["gate_ok_drawdown"]) is True

    def test_at_threshold_passes(self):
        df = _agg_df(_agg_row(dd_min=-0.50))
        result = apply_candidate_gates(df, _cfg(cand_max_dd=-0.50))
        assert bool(result.iloc[0]["gate_ok_drawdown"]) is True

    def test_below_threshold_fails(self):
        df = _agg_df(_agg_row(dd_min=-0.60))
        result = apply_candidate_gates(df, _cfg(cand_max_dd=-0.50))
        assert bool(result.iloc[0]["gate_ok_drawdown"]) is False


class TestCandidateGateWorstSegment:
    def test_disabled_when_none_gives_na(self):
        """Plan §4.5/G1: disabled worst_segment → gate_ok_worst_segment = pd.NA (not True)."""
        df = _agg_df(_agg_row(pnl_min=-100.0))
        result = apply_candidate_gates(df, _cfg(cand_worst_segment=None))
        assert result.iloc[0]["gate_ok_worst_segment"] is pd.NA

    def test_disabled_sets_worst_segment_gate_enabled_false(self):
        df = _agg_df(_agg_row())
        result = apply_candidate_gates(df, _cfg(cand_worst_segment=None))
        assert bool(result.iloc[0]["worst_segment_gate_enabled"]) is False

    def test_enabled_sets_worst_segment_gate_enabled_true(self):
        df = _agg_df(_agg_row())
        result = apply_candidate_gates(df, _cfg(cand_worst_segment=-1.0))
        assert bool(result.iloc[0]["worst_segment_gate_enabled"]) is True

    def test_enabled_passes(self):
        df = _agg_df(_agg_row(pnl_min=0.5))
        result = apply_candidate_gates(df, _cfg(cand_worst_segment=-1.0))
        assert bool(result.iloc[0]["gate_ok_worst_segment"]) is True

    def test_enabled_fails(self):
        df = _agg_df(_agg_row(pnl_min=-2.0))
        result = apply_candidate_gates(df, _cfg(cand_worst_segment=-1.0))
        assert bool(result.iloc[0]["gate_ok_worst_segment"]) is False


# ===========================================================================
# n_ok_steps == 0 → everything False (reviewer note)
# ===========================================================================

class TestNoOkSteps:
    def test_zero_ok_all_gates_false_worst_segment_enabled(self):
        """n_ok_steps==0: all evaluated gates False; worst_segment enabled → False."""
        df = _agg_df(_agg_row(n_ok=0, n_total=3, pnl_median=np.nan, trades_median=np.nan,
                               pnl_min=np.nan, dd_min=np.nan))
        result = apply_candidate_gates(df, _cfg(cand_worst_segment=-1.0))
        row = result.iloc[0]
        assert bool(row["gate_ok_positive_median"]) is False
        assert bool(row["gate_ok_min_trades"]) is False
        assert bool(row["gate_ok_drawdown"]) is False
        assert bool(row["gate_ok_worst_segment"]) is False
        assert bool(row["seed_gate_passed"]) is False
        assert row["seed_gate_fail_reason"] == "no_ok_steps"

    def test_zero_ok_worst_segment_disabled_gives_na(self):
        """n_ok_steps==0 + disabled worst_segment → gate_ok_worst_segment = pd.NA."""
        df = _agg_df(_agg_row(n_ok=0, n_total=3, pnl_median=np.nan, trades_median=np.nan,
                               pnl_min=np.nan, dd_min=np.nan))
        result = apply_candidate_gates(df, _cfg())   # default: cand_worst_segment=None
        row = result.iloc[0]
        # Disabled worst_segment stays pd.NA even when n_ok_steps == 0.
        assert row["gate_ok_worst_segment"] is pd.NA
        # seed_gate_passed must still be bool False (not NA) and fail_reason clear.
        assert bool(row["seed_gate_passed"]) is False
        assert row["seed_gate_fail_reason"] == "no_ok_steps"


# ===========================================================================
# Composite (§6.3)
# ===========================================================================

class TestComposite:
    def test_all_pass_seed_gate_true(self):
        df = _agg_df(_agg_row(pnl_median=5.0, trades_median=10.0, pnl_min=1.0, dd_min=-0.20))
        result = apply_candidate_gates(df, _cfg())
        row = result.iloc[0]
        assert bool(row["seed_gate_passed"]) is True
        assert row["seed_gate_fail_reason"] == ""

    def test_one_fails_seed_gate_false(self):
        # Only positive_median fails (median == 0.0, threshold 0.0 → strictly >)
        df = _agg_df(_agg_row(pnl_median=0.0, trades_median=10.0, pnl_min=1.0, dd_min=-0.20))
        result = apply_candidate_gates(df, _cfg(cand_positive_median=0.0))
        row = result.iloc[0]
        assert bool(row["seed_gate_passed"]) is False
        assert "gate_ok_positive_median" in row["seed_gate_fail_reason"]

    def test_multiple_fail_comma_separated(self):
        df = _agg_df(_agg_row(pnl_median=-1.0, trades_median=1.0, pnl_min=1.0, dd_min=-0.20))
        result = apply_candidate_gates(df, _cfg(cand_min_trades_median=3.0))
        row = result.iloc[0]
        assert bool(row["seed_gate_passed"]) is False
        reasons = row["seed_gate_fail_reason"].split(",")
        assert "gate_ok_positive_median" in reasons
        assert "gate_ok_min_trades" in reasons

    def test_worst_segment_disabled_not_in_reasons(self):
        # worst_segment disabled (None) → not listed in fail reasons even if pnl_min negative
        df = _agg_df(_agg_row(pnl_median=5.0, trades_median=10.0, pnl_min=-100.0, dd_min=-0.20))
        result = apply_candidate_gates(df, _cfg(cand_worst_segment=None))
        row = result.iloc[0]
        assert bool(row["seed_gate_passed"]) is True
        assert "gate_ok_worst_segment" not in row["seed_gate_fail_reason"]


# ===========================================================================
# Copy semantics for candidate gates
# ===========================================================================

class TestCandidateCopySemantics:
    def test_original_not_mutated(self):
        df = _agg_df(_agg_row())
        cols_before = list(df.columns)
        _ = apply_candidate_gates(df, _cfg())
        assert list(df.columns) == cols_before
        assert "seed_gate_passed" not in df.columns


# ===========================================================================
# Multiple candidates
# ===========================================================================

class TestMultipleCandidates:
    def test_mixed_pass_fail(self):
        df = _agg_df(
            _agg_row("gp1", pnl_median=5.0, trades_median=10.0, pnl_min=1.0, dd_min=-0.10),
            _agg_row("gp2", pnl_median=-1.0, trades_median=10.0, pnl_min=-5.0, dd_min=-0.10),
            _agg_row("gp3", n_ok=0, n_total=3, pnl_median=np.nan, trades_median=np.nan,
                      pnl_min=np.nan, dd_min=np.nan),
        )
        result = apply_candidate_gates(df, _cfg())
        assert bool(result.iloc[0]["seed_gate_passed"]) is True
        assert bool(result.iloc[1]["seed_gate_passed"]) is False
        assert bool(result.iloc[2]["seed_gate_passed"]) is False
        assert result.iloc[2]["seed_gate_fail_reason"] == "no_ok_steps"


# ===========================================================================
# Gate columns always present
# ===========================================================================

class TestGateColumnsPresent:
    def test_all_gate_columns_in_output(self):
        df = _agg_df(_agg_row())
        result = apply_candidate_gates(df, _cfg())
        for col in _CANDIDATE_GATE_COLUMNS:
            assert col in result.columns, f"Missing gate column: {col}"


# ===========================================================================
# FIX-2.3 — max_drawdown sign warning in apply_step_gates
# ===========================================================================

class TestMaxDrawdownSignWarningStepGates:
    """Defensive guard in step gates: positive max_drawdown warns about pipeline-order issues."""

    def test_positive_max_drawdown_logs_warning(self, caplog):
        """ok step with max_drawdown=0.05 → warning logged."""
        import logging
        df = _step_df(_step_row(max_drawdown=0.05))
        with caplog.at_level(logging.WARNING, logger="wf_grid.gates.gates"):
            apply_step_gates(df, _cfg())
        assert any("max_drawdown > 0" in r.message for r in caplog.records)

    def test_negative_max_drawdown_no_warning(self, caplog):
        """ok step with max_drawdown=-0.10 → no warning."""
        import logging
        df = _step_df(_step_row(max_drawdown=-0.10))
        with caplog.at_level(logging.WARNING, logger="wf_grid.gates.gates"):
            apply_step_gates(df, _cfg())
        assert not any("max_drawdown > 0" in r.message for r in caplog.records)

    def test_zero_max_drawdown_no_warning(self, caplog):
        """ok step with max_drawdown=0.0 → no warning (boundary is valid)."""
        import logging
        df = _step_df(_step_row(max_drawdown=0.0))
        with caplog.at_level(logging.WARNING, logger="wf_grid.gates.gates"):
            apply_step_gates(df, _cfg())
        assert not any("max_drawdown > 0" in r.message for r in caplog.records)

    def test_positive_dd_in_non_ok_row_no_warning(self, caplog):
        """Non-ok row with positive max_drawdown must not trigger warning."""
        import logging
        df = _step_df(_step_row(status=StepStatus.NO_TRADES.value, max_drawdown=0.50))
        with caplog.at_level(logging.WARNING, logger="wf_grid.gates.gates"):
            apply_step_gates(df, _cfg())
        assert not any("max_drawdown > 0" in r.message for r in caplog.records)

    def test_positive_dd_warning_does_not_change_status(self, caplog):
        """In direct gate-unit usage (without contracts), sign warning alone does not flip status."""
        import logging
        # max_drawdown=0.05 is positive but gate checks max_drawdown < threshold (e.g. -0.50)
        # 0.05 < -0.50 is False → gate should pass on drawdown criterion.
        # Contract fail-closed is enforced upstream in metric_contracts; this test
        # verifies gate-layer behavior in isolation.
        df = _step_df(_step_row(num_trades=10, max_drawdown=0.05))
        with caplog.at_level(logging.WARNING, logger="wf_grid.gates.gates"):
            result = apply_step_gates(df, _cfg(step_max_dd=-0.50))
        # Warning is logged
        assert any("max_drawdown > 0" in r.message for r in caplog.records)
        # But step still passes the drawdown gate (0.05 is not < -0.50)
        assert result.iloc[0]["step_status"] == StepStatus.OK.value


# ===========================================================================
# FIX-4.1 — Minimum total trades candidate gate
# ===========================================================================

class TestCandidateGateMinTotalTrades:
    """FIX-4.1: gate_ok_min_total_trades based on total_oos_trades (from aggregation)."""

    def test_above_threshold_passes(self):
        df = _agg_df(_agg_row(total_oos_trades=50))
        result = apply_candidate_gates(df, _cfg(cand_min_total_trades=30))
        assert bool(result.iloc[0]["gate_ok_min_total_trades"]) is True

    def test_at_threshold_passes(self):
        df = _agg_df(_agg_row(total_oos_trades=30))
        result = apply_candidate_gates(df, _cfg(cand_min_total_trades=30))
        assert bool(result.iloc[0]["gate_ok_min_total_trades"]) is True

    def test_below_threshold_fails(self):
        df = _agg_df(_agg_row(total_oos_trades=15))
        result = apply_candidate_gates(df, _cfg(cand_min_total_trades=30))
        assert bool(result.iloc[0]["gate_ok_min_total_trades"]) is False

    def test_zero_trades_fails(self):
        df = _agg_df(_agg_row(total_oos_trades=0))
        result = apply_candidate_gates(df, _cfg(cand_min_total_trades=30))
        assert bool(result.iloc[0]["gate_ok_min_total_trades"]) is False

    def test_custom_threshold(self):
        df = _agg_df(_agg_row(total_oos_trades=12))
        result = apply_candidate_gates(df, _cfg(cand_min_total_trades=10))
        assert bool(result.iloc[0]["gate_ok_min_total_trades"]) is True

    def test_no_ok_steps_always_false(self):
        df = _agg_df(_agg_row(n_ok=0, n_total=3, pnl_median=np.nan,
                               trades_median=np.nan, pnl_min=np.nan,
                               dd_min=np.nan, total_oos_trades=0))
        result = apply_candidate_gates(df, _cfg())
        assert bool(result.iloc[0]["gate_ok_min_total_trades"]) is False

    def test_included_in_composite(self):
        """Failing min_total_trades must cause seed_gate_passed=False."""
        df = _agg_df(_agg_row(pnl_median=5.0, trades_median=10.0,
                               pnl_min=1.0, dd_min=-0.20,
                               total_oos_trades=15))
        result = apply_candidate_gates(df, _cfg(cand_min_total_trades=30))
        assert bool(result.iloc[0]["seed_gate_passed"]) is False
        assert "gate_ok_min_total_trades" in result.iloc[0]["seed_gate_fail_reason"]

    def test_passing_all_gates_with_total_trades(self):
        """All gates pass including min_total_trades → seed_gate_passed=True."""
        df = _agg_df(_agg_row(pnl_median=5.0, trades_median=10.0,
                               pnl_min=1.0, dd_min=-0.20,
                               total_oos_trades=50))
        result = apply_candidate_gates(df, _cfg(cand_min_total_trades=30))
        assert bool(result.iloc[0]["seed_gate_passed"]) is True
        assert result.iloc[0]["seed_gate_fail_reason"] == ""


# ===========================================================================
# FIX-2 — gate_ok_coverage: ok_ratio >= min_ok_ratio
# ===========================================================================

class TestCoverageGate:
    """gate_ok_coverage enforces survivorship guard (ok_ratio >= min_ok_ratio)."""

    def test_ok_ratio_above_threshold_passes(self):
        """ok_ratio=0.8 >= 0.7 → gate passes."""
        df = _agg_df(_agg_row(n_ok=4, n_total=5))  # ok_ratio = 0.8
        result = apply_candidate_gates(df, _cfg(cand_min_ok_ratio=0.7))
        assert bool(result.iloc[0]["gate_ok_coverage"]) is True

    def test_ok_ratio_at_threshold_passes(self):
        """ok_ratio exactly at threshold → gate passes (>=)."""
        df = _agg_df(_agg_row(n_ok=7, n_total=10))  # ok_ratio = 0.7
        result = apply_candidate_gates(df, _cfg(cand_min_ok_ratio=0.7))
        assert bool(result.iloc[0]["gate_ok_coverage"]) is True

    def test_ok_ratio_below_threshold_fails(self):
        """ok_ratio=0.5 < 0.7 → gate fails."""
        df = _agg_df(_agg_row(n_ok=1, n_total=2))  # ok_ratio = 0.5
        result = apply_candidate_gates(df, _cfg(cand_min_ok_ratio=0.7))
        assert bool(result.iloc[0]["gate_ok_coverage"]) is False

    def test_coverage_fail_blocks_seed_gate_passed(self):
        """coverage gate failure must propagate to seed_gate_passed=False."""
        df = _agg_df(_agg_row(n_ok=1, n_total=3))  # ok_ratio ≈ 0.33
        result = apply_candidate_gates(df, _cfg(cand_min_ok_ratio=0.7))
        assert bool(result.iloc[0]["seed_gate_passed"]) is False

    def test_coverage_fail_in_fail_reason(self):
        """gate_ok_coverage failure must appear in seed_gate_fail_reason."""
        df = _agg_df(_agg_row(n_ok=1, n_total=3))  # ok_ratio ≈ 0.33
        result = apply_candidate_gates(df, _cfg(cand_min_ok_ratio=0.7))
        assert "gate_ok_coverage" in result.iloc[0]["seed_gate_fail_reason"]

    def test_zero_threshold_always_passes(self):
        """min_ok_ratio=0.0 effectively disables the gate — even ok_ratio=0 passes."""
        df = _agg_df(_agg_row(n_ok=1, n_total=5))  # ok_ratio=0.2
        result = apply_candidate_gates(df, _cfg(cand_min_ok_ratio=0.0))
        assert bool(result.iloc[0]["gate_ok_coverage"]) is True

    def test_custom_threshold_works(self):
        """min_ok_ratio=0.9 rejects ok_ratio=0.8 candidate."""
        df = _agg_df(_agg_row(n_ok=8, n_total=10))  # ok_ratio=0.8
        result = apply_candidate_gates(df, _cfg(cand_min_ok_ratio=0.9))
        assert bool(result.iloc[0]["gate_ok_coverage"]) is False

    def test_no_ok_steps_still_fail(self):
        """n_ok_steps=0 → coverage gate False regardless of threshold."""
        df = _agg_df(_agg_row(n_ok=0, n_total=3, pnl_median=np.nan,
                               trades_median=np.nan, pnl_min=np.nan, dd_min=np.nan))
        result = apply_candidate_gates(df, _cfg(cand_min_ok_ratio=0.0))
        assert bool(result.iloc[0]["gate_ok_coverage"]) is False

    def test_gate_ok_coverage_in_candidate_gate_columns(self):
        """gate_ok_coverage must be listed in _CANDIDATE_GATE_COLUMNS."""
        assert "gate_ok_coverage" in _CANDIDATE_GATE_COLUMNS

    def test_gate_ok_coverage_column_present_in_result(self):
        """gate_ok_coverage column must appear in apply_candidate_gates output."""
        df = _agg_df(_agg_row())
        result = apply_candidate_gates(df, _cfg())
        assert "gate_ok_coverage" in result.columns
