"""
Unit tests for A3: assign_step_status, assign_candidate_status.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from wf_grid.config.loader import load_grid_config
from wf_grid.config.schema import GridConfig, StatusConfig
from wf_grid.status.status_model import (
    CandidateStatus,
    StepStatus,
    assign_candidate_status,
    assign_step_status,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(tmp_path: Path, content: str) -> str:
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return str(p)


MINIMAL_YAML = """\
data:
  file_path: data.csv
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
"""


def _config(tmp_path, min_meaningful_bars: int = 30) -> GridConfig:
    yaml_text = f"""\
data:
  file_path: data.csv
validation:
  walk_forward:
    train_size: "90D"
    test_size: "30D"
status:
  min_meaningful_bars: {min_meaningful_bars}
"""
    path = _write_yaml(tmp_path, yaml_text)
    return load_grid_config(path)


def _metrics(num_trades: int = 5) -> dict:
    return {"num_trades": num_trades, "sum_pnl_pct": 0.05}


# ===========================================================================
# assign_step_status — §3.2
# ===========================================================================

class TestAssignStepStatusOk:
    def test_ok_with_sufficient_bars_and_trades(self, tmp_path):
        cfg = _config(tmp_path, min_meaningful_bars=30)
        status = assign_step_status(_metrics(num_trades=5), effective_oos_bars=30, config=cfg)
        assert status == StepStatus.OK

    def test_ok_exactly_at_min_bars(self, tmp_path):
        cfg = _config(tmp_path, min_meaningful_bars=30)
        status = assign_step_status(_metrics(num_trades=1), effective_oos_bars=30, config=cfg)
        assert status == StepStatus.OK

    def test_ok_large_bars_and_trades(self, tmp_path):
        cfg = _config(tmp_path, min_meaningful_bars=30)
        status = assign_step_status(_metrics(num_trades=100), effective_oos_bars=1000, config=cfg)
        assert status == StepStatus.OK


class TestAssignStepStatusInsufficientBars:
    def test_insufficient_bars_one_below_threshold(self, tmp_path):
        cfg = _config(tmp_path, min_meaningful_bars=30)
        status = assign_step_status(_metrics(num_trades=5), effective_oos_bars=29, config=cfg)
        assert status == StepStatus.INSUFFICIENT_BARS

    def test_insufficient_bars_zero(self, tmp_path):
        cfg = _config(tmp_path, min_meaningful_bars=30)
        status = assign_step_status(_metrics(num_trades=5), effective_oos_bars=0, config=cfg)
        assert status == StepStatus.INSUFFICIENT_BARS

    def test_insufficient_bars_takes_priority_over_no_trades(self, tmp_path):
        # insufficient_bars check comes before no_trades check (priority §3.2)
        cfg = _config(tmp_path, min_meaningful_bars=30)
        status = assign_step_status(_metrics(num_trades=0), effective_oos_bars=5, config=cfg)
        assert status == StepStatus.INSUFFICIENT_BARS

    def test_custom_min_meaningful_bars(self, tmp_path):
        cfg = _config(tmp_path, min_meaningful_bars=50)
        assert assign_step_status(_metrics(), effective_oos_bars=49, config=cfg) == StepStatus.INSUFFICIENT_BARS
        assert assign_step_status(_metrics(), effective_oos_bars=50, config=cfg) == StepStatus.OK


class TestAssignStepStatusNoTrades:
    def test_no_trades_zero(self, tmp_path):
        cfg = _config(tmp_path, min_meaningful_bars=30)
        status = assign_step_status(_metrics(num_trades=0), effective_oos_bars=100, config=cfg)
        assert status == StepStatus.NO_TRADES

    def test_no_trades_missing_key_defaults_zero(self, tmp_path):
        # num_trades key absent → defaults to 0 → no_trades
        cfg = _config(tmp_path, min_meaningful_bars=30)
        status = assign_step_status({}, effective_oos_bars=100, config=cfg)
        assert status == StepStatus.NO_TRADES

    def test_one_trade_is_ok(self, tmp_path):
        cfg = _config(tmp_path, min_meaningful_bars=30)
        status = assign_step_status(_metrics(num_trades=1), effective_oos_bars=100, config=cfg)
        assert status == StepStatus.OK


class TestAssignStepStatusDeterminism:
    def test_same_inputs_same_output(self, tmp_path):
        cfg = _config(tmp_path)
        m = _metrics(num_trades=5)
        r1 = assign_step_status(m, 50, cfg)
        r2 = assign_step_status(m, 50, cfg)
        assert r1 == r2

    def test_extra_metric_keys_ignored(self, tmp_path):
        cfg = _config(tmp_path)
        metrics = {"num_trades": 3, "sharpe": 1.5, "max_drawdown": -0.1, "unknown_key": "x"}
        status = assign_step_status(metrics, effective_oos_bars=50, config=cfg)
        assert status == StepStatus.OK


# ===========================================================================
# assign_candidate_status — §3.3
# ===========================================================================

class TestAssignCandidateStatusOk:
    def test_all_ok(self):
        statuses = [StepStatus.OK, StepStatus.OK, StepStatus.OK]
        assert assign_candidate_status(statuses) == CandidateStatus.OK

    def test_single_ok(self):
        assert assign_candidate_status([StepStatus.OK]) == CandidateStatus.OK


class TestAssignCandidateStatusFailed:
    def test_all_no_trades(self):
        statuses = [StepStatus.NO_TRADES, StepStatus.NO_TRADES]
        assert assign_candidate_status(statuses) == CandidateStatus.FAILED

    def test_all_insufficient_bars(self):
        statuses = [StepStatus.INSUFFICIENT_BARS] * 3
        assert assign_candidate_status(statuses) == CandidateStatus.FAILED

    def test_all_runtime_error(self):
        statuses = [StepStatus.RUNTIME_ERROR] * 2
        assert assign_candidate_status(statuses) == CandidateStatus.FAILED

    def test_all_gate_failed(self):
        statuses = [StepStatus.GATE_FAILED] * 4
        assert assign_candidate_status(statuses) == CandidateStatus.FAILED

    def test_all_invalid(self):
        assert assign_candidate_status([StepStatus.INVALID]) == CandidateStatus.FAILED

    def test_mixed_non_ok(self):
        statuses = [StepStatus.NO_TRADES, StepStatus.INSUFFICIENT_BARS, StepStatus.RUNTIME_ERROR]
        assert assign_candidate_status(statuses) == CandidateStatus.FAILED

    def test_empty_iterable(self):
        assert assign_candidate_status([]) == CandidateStatus.FAILED


class TestAssignCandidateStatusPartial:
    def test_one_ok_rest_no_trades(self):
        statuses = [StepStatus.OK, StepStatus.NO_TRADES, StepStatus.NO_TRADES]
        assert assign_candidate_status(statuses) == CandidateStatus.PARTIAL

    def test_one_ok_rest_insufficient(self):
        statuses = [StepStatus.OK, StepStatus.INSUFFICIENT_BARS]
        assert assign_candidate_status(statuses) == CandidateStatus.PARTIAL

    def test_ok_and_gate_failed(self):
        statuses = [StepStatus.OK, StepStatus.GATE_FAILED]
        assert assign_candidate_status(statuses) == CandidateStatus.PARTIAL

    def test_ok_and_runtime_error(self):
        statuses = [StepStatus.OK, StepStatus.RUNTIME_ERROR]
        assert assign_candidate_status(statuses) == CandidateStatus.PARTIAL

    def test_mixed_ok_and_multiple_non_ok(self):
        statuses = [
            StepStatus.OK,
            StepStatus.NO_TRADES,
            StepStatus.OK,
            StepStatus.GATE_FAILED,
        ]
        assert assign_candidate_status(statuses) == CandidateStatus.PARTIAL

    def test_many_steps_one_ok(self):
        statuses = [StepStatus.NO_TRADES] * 9 + [StepStatus.OK]
        assert assign_candidate_status(statuses) == CandidateStatus.PARTIAL


class TestAssignCandidateStatusDeterminism:
    def test_same_inputs_same_output(self):
        statuses = [StepStatus.OK, StepStatus.NO_TRADES]
        assert assign_candidate_status(statuses) == assign_candidate_status(statuses)

    def test_generator_input_accepted(self):
        # Must also accept generators / iterables, not just lists
        gen = (s for s in [StepStatus.OK, StepStatus.OK])
        assert assign_candidate_status(gen) == CandidateStatus.OK

    def test_tuple_input_accepted(self):
        assert assign_candidate_status((StepStatus.OK,)) == CandidateStatus.OK


# ===========================================================================
# Full transition table — all step-status values participate in candidate
# ===========================================================================

class TestAllStepStatusesInCandidate:
    """Every StepStatus value other than OK must push candidate toward FAILED/PARTIAL."""

    non_ok_statuses = [
        StepStatus.SKIPPED,
        StepStatus.NO_TRADES,
        StepStatus.INSUFFICIENT_BARS,
        StepStatus.INVALID,
        StepStatus.GATE_FAILED,
        StepStatus.RUNTIME_ERROR,
    ]

    @pytest.mark.parametrize("bad", non_ok_statuses)
    def test_single_non_ok_is_failed(self, bad):
        assert assign_candidate_status([bad]) == CandidateStatus.FAILED

    @pytest.mark.parametrize("bad", non_ok_statuses)
    def test_ok_plus_non_ok_is_partial(self, bad):
        assert assign_candidate_status([StepStatus.OK, bad]) == CandidateStatus.PARTIAL
