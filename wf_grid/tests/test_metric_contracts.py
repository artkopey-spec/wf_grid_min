"""
Unit tests for validate_metric_contracts (§4.3 of the fix plan, M1/M2/M3).

Covers every acceptance criterion from the plan's Unit Tests list for
metric contracts:

  - positive max_drawdown on ok step fails closed (M1);
  - NaN max_drawdown fails (M2);
  - inf hard metric fails (M2);
  - INVALID_METRIC_VALUE in hard metric fails (M2);
  - INVALID_METRIC_VALUE in soft metric fails (M2);
  - legitimate NaN profit_factor does NOT invalidate ok-step (M2);
  - legitimate NaN sortino does NOT invalidate ok-step (M2);
  - missing sum_pnl_pct / num_trades / max_drawdown fails instead of
    defaulting (M3).

Non-ok rows (gate_failed, runtime_error, insufficient_bars, ...) must pass
through untouched.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from wf_grid.collect.metric_contracts import (
    HARD_REQUIRED_METRICS,
    SOFT_METRICS,
    validate_metric_contracts,
)
from wf_grid.config.schema import INVALID_METRIC_VALUE
from wf_grid.status.status_model import StepStatus


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_ALL_METRIC_COLS = [
    "sum_pnl_pct", "max_drawdown", "num_trades",
    "win_rate", "profit_factor", "avg_trade",
    "sharpe", "sortino", "cagr",
]


def _ok_row(**overrides) -> dict:
    """Return a dict with a clean ok-step (all contracts satisfied)."""
    row = {
        "grid_point_id": "atr5_m2.5_both",
        "wf_step": 1,
        "step_status": StepStatus.OK.value,
        "sum_pnl_pct": 0.10,
        "max_drawdown": -0.05,
        "num_trades": 5,
        "win_rate": 0.60,
        "profit_factor": 1.5,
        "avg_trade": 0.02,
        "sharpe": 1.1,
        "sortino": 1.3,
        "cagr": 0.12,
        "error_type": None,
        "error_message": None,
    }
    row.update(overrides)
    return row


def _df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Metric set definitions — guard against accidental rename / drift
# ---------------------------------------------------------------------------

class TestMetricSets:
    def test_hard_metrics_exact_set(self):
        assert set(HARD_REQUIRED_METRICS) == {
            "sum_pnl_pct", "max_drawdown", "num_trades",
        }

    def test_soft_metrics_exact_set(self):
        assert set(SOFT_METRICS) == {
            "win_rate", "profit_factor", "avg_trade",
            "sharpe", "sortino", "cagr",
        }

    def test_hard_and_soft_are_disjoint(self):
        assert set(HARD_REQUIRED_METRICS).isdisjoint(set(SOFT_METRICS))


# ---------------------------------------------------------------------------
# Happy path: clean ok-row passes through unchanged
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_clean_ok_row_unchanged(self):
        df = _df([_ok_row()])
        out = validate_metric_contracts(df)
        assert out["step_status"].tolist() == [StepStatus.OK.value]
        assert out["error_type"].isna().all() or out["error_type"].iloc[0] is None
        assert out["error_message"].isna().all() or out["error_message"].iloc[0] is None

    def test_many_clean_ok_rows_unchanged(self):
        df = _df([_ok_row(wf_step=i) for i in range(1, 6)])
        out = validate_metric_contracts(df)
        assert (out["step_status"] == StepStatus.OK.value).all()

    def test_returns_copy_not_mutating_input(self):
        df = _df([_ok_row(sum_pnl_pct=np.nan)])
        before = df.copy(deep=True)
        _ = validate_metric_contracts(df)
        # Original df must be unchanged
        pd.testing.assert_frame_equal(df, before)


# ---------------------------------------------------------------------------
# M1: positive max_drawdown on ok step fails closed
# ---------------------------------------------------------------------------

class TestPositiveMaxDrawdownFailsClosed:
    def test_positive_dd_invalidates_ok_step(self):
        df = _df([_ok_row(max_drawdown=0.05)])
        out = validate_metric_contracts(df)
        assert out["step_status"].iloc[0] == StepStatus.INVALID.value
        assert out["error_type"].iloc[0] == "metric_contract"

    def test_positive_dd_error_message_mentions_drawdown(self):
        df = _df([_ok_row(max_drawdown=0.10)])
        out = validate_metric_contracts(df)
        msg = out["error_message"].iloc[0]
        assert "max_drawdown" in msg

    def test_zero_dd_is_allowed(self):
        """max_drawdown == 0 is the boundary case and must NOT fail."""
        df = _df([_ok_row(max_drawdown=0.0)])
        out = validate_metric_contracts(df)
        assert out["step_status"].iloc[0] == StepStatus.OK.value

    def test_negative_dd_is_allowed(self):
        df = _df([_ok_row(max_drawdown=-0.2)])
        out = validate_metric_contracts(df)
        assert out["step_status"].iloc[0] == StepStatus.OK.value


# ---------------------------------------------------------------------------
# M2: NaN / inf / INVALID in hard metrics → invalid
# ---------------------------------------------------------------------------

class TestHardMetricNaN:
    @pytest.mark.parametrize("hard_metric", HARD_REQUIRED_METRICS)
    def test_nan_in_hard_metric_invalidates(self, hard_metric):
        df = _df([_ok_row(**{hard_metric: np.nan})])
        out = validate_metric_contracts(df)
        assert out["step_status"].iloc[0] == StepStatus.INVALID.value
        assert out["error_type"].iloc[0] == "metric_contract"
        assert hard_metric in out["error_message"].iloc[0]

    def test_none_in_hard_metric_invalidates(self):
        df = _df([_ok_row(sum_pnl_pct=None)])
        out = validate_metric_contracts(df)
        assert out["step_status"].iloc[0] == StepStatus.INVALID.value


class TestHardMetricInf:
    @pytest.mark.parametrize("hard_metric", HARD_REQUIRED_METRICS)
    def test_positive_inf_in_hard_invalidates(self, hard_metric):
        df = _df([_ok_row(**{hard_metric: math.inf})])
        out = validate_metric_contracts(df)
        assert out["step_status"].iloc[0] == StepStatus.INVALID.value
        assert "finite" in out["error_message"].iloc[0].lower()

    @pytest.mark.parametrize("hard_metric", HARD_REQUIRED_METRICS)
    def test_negative_inf_in_hard_invalidates(self, hard_metric):
        df = _df([_ok_row(**{hard_metric: -math.inf})])
        out = validate_metric_contracts(df)
        assert out["step_status"].iloc[0] == StepStatus.INVALID.value


class TestHardMetricSentinel:
    @pytest.mark.parametrize("hard_metric", HARD_REQUIRED_METRICS)
    def test_invalid_metric_value_in_hard_invalidates(self, hard_metric):
        df = _df([_ok_row(**{hard_metric: INVALID_METRIC_VALUE})])
        out = validate_metric_contracts(df)
        assert out["step_status"].iloc[0] == StepStatus.INVALID.value
        assert "INVALID_METRIC_VALUE" in out["error_message"].iloc[0]


# ---------------------------------------------------------------------------
# M2: soft metrics — sentinel fails, NaN is legitimate
# ---------------------------------------------------------------------------

class TestSoftMetricNaNLegitimate:
    def test_nan_profit_factor_on_ok_step_preserved(self):
        """Legitimate NaN profit_factor (e.g. no losers) must NOT invalidate."""
        df = _df([_ok_row(profit_factor=np.nan)])
        out = validate_metric_contracts(df)
        assert out["step_status"].iloc[0] == StepStatus.OK.value

    def test_nan_sortino_on_ok_step_preserved(self):
        df = _df([_ok_row(sortino=np.nan)])
        out = validate_metric_contracts(df)
        assert out["step_status"].iloc[0] == StepStatus.OK.value

    @pytest.mark.parametrize("soft_metric", SOFT_METRICS)
    def test_nan_in_any_soft_metric_preserved(self, soft_metric):
        df = _df([_ok_row(**{soft_metric: np.nan})])
        out = validate_metric_contracts(df)
        assert out["step_status"].iloc[0] == StepStatus.OK.value


class TestSoftMetricSentinel:
    @pytest.mark.parametrize("soft_metric", SOFT_METRICS)
    def test_invalid_metric_value_in_soft_invalidates(self, soft_metric):
        df = _df([_ok_row(**{soft_metric: INVALID_METRIC_VALUE})])
        out = validate_metric_contracts(df)
        assert out["step_status"].iloc[0] == StepStatus.INVALID.value
        assert soft_metric in out["error_message"].iloc[0]


# ---------------------------------------------------------------------------
# M3: missing required metrics fail (no silent defaulting)
# ---------------------------------------------------------------------------

class TestMissingRequiredMetrics:
    @pytest.mark.parametrize("hard_metric", HARD_REQUIRED_METRICS)
    def test_missing_column_invalidates_ok_step(self, hard_metric):
        """When the hard-required column is entirely absent from the DF."""
        base = _ok_row()
        del base[hard_metric]
        df = _df([base])
        out = validate_metric_contracts(df)
        assert out["step_status"].iloc[0] == StepStatus.INVALID.value
        assert out["error_type"].iloc[0] == "metric_contract"

    def test_missing_does_not_silently_default_to_zero(self):
        """sum_pnl_pct missing must fail, not become 0.0 and pass gates."""
        base = _ok_row()
        del base["sum_pnl_pct"]
        df = _df([base])
        out = validate_metric_contracts(df)
        # Must NOT be ok with sum_pnl_pct == 0 silently inserted
        assert out["step_status"].iloc[0] != StepStatus.OK.value


# ---------------------------------------------------------------------------
# Non-ok rows are never mutated by the validator
# ---------------------------------------------------------------------------

class TestNonOkRowsUntouched:
    @pytest.mark.parametrize("status", [
        StepStatus.GATE_FAILED,
        StepStatus.RUNTIME_ERROR,
        StepStatus.INSUFFICIENT_BARS,
        StepStatus.NO_TRADES,
        StepStatus.INVALID,   # already invalid stays invalid
        StepStatus.SKIPPED,
    ])
    def test_non_ok_row_with_broken_metrics_is_untouched(self, status):
        """Non-ok rows can have NaN/inf metrics — validator must not touch them."""
        df = _df([_ok_row(
            step_status=status.value,
            sum_pnl_pct=np.nan,
            max_drawdown=0.5,   # positive — would trigger M1 if this were ok
            num_trades=math.inf,
        )])
        out = validate_metric_contracts(df)
        assert out["step_status"].iloc[0] == status.value


# ---------------------------------------------------------------------------
# Mixed DataFrames — only ok rows get re-tagged
# ---------------------------------------------------------------------------

class TestMixedRows:
    def test_only_bad_ok_row_is_invalidated(self):
        df = _df([
            _ok_row(wf_step=1),                               # clean ok
            _ok_row(wf_step=2, max_drawdown=0.1),             # bad ok → invalid
            _ok_row(wf_step=3, step_status=StepStatus.GATE_FAILED.value,
                    max_drawdown=0.1),                        # non-ok, untouched
        ])
        out = validate_metric_contracts(df)
        assert out.loc[0, "step_status"] == StepStatus.OK.value
        assert out.loc[1, "step_status"] == StepStatus.INVALID.value
        assert out.loc[1, "error_type"] == "metric_contract"
        assert out.loc[2, "step_status"] == StepStatus.GATE_FAILED.value

    def test_empty_frame_passes_through(self):
        df = pd.DataFrame(columns=["step_status"] + _ALL_METRIC_COLS)
        out = validate_metric_contracts(df)
        assert out.empty
        assert list(out.columns) == list(df.columns)


# ---------------------------------------------------------------------------
# Contract: output schema is stable
# ---------------------------------------------------------------------------

class TestOutputSchema:
    def test_row_count_preserved(self):
        df = _df([_ok_row(wf_step=i) for i in range(1, 4)])
        out = validate_metric_contracts(df)
        assert len(out) == len(df)

    def test_column_set_preserved(self):
        df = _df([_ok_row()])
        out = validate_metric_contracts(df)
        assert set(out.columns) == set(df.columns)

    def test_error_type_literal_is_metric_contract(self):
        """Contract violation must use the exact string 'metric_contract' per plan."""
        df = _df([_ok_row(max_drawdown=1.0)])
        out = validate_metric_contracts(df)
        assert out["error_type"].iloc[0] == "metric_contract"


# ---------------------------------------------------------------------------
# Contract: invalid (contract violation) is NOT converted to gate_failed
# ---------------------------------------------------------------------------

class TestNotGateFailed:
    def test_contract_violation_is_invalid_not_gate_failed(self):
        """Per plan §4.1: positive DD on ok must be 'invalid', not 'gate_failed'."""
        df = _df([_ok_row(max_drawdown=0.2)])
        out = validate_metric_contracts(df)
        assert out["step_status"].iloc[0] == StepStatus.INVALID.value
        assert out["step_status"].iloc[0] != StepStatus.GATE_FAILED.value
