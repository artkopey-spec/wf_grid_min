"""§10.2 Runtime tests (donor TESTER side) for exit_b_immediate_off (Plan v3).

Purpose: from the donor TESTER branch, re-assert the same apply()-level
invariants (using the same shared fixtures as wf_grid). The CANONICAL
cross-branch parity test (§10.2.G) lives in
``wf_grid/tests/test_pr_exit_b_immediate_off.py::TestImmediateOffCrossBranchParity``
which compares run_single_backtest (WF) vs run_period (Tester) on the same
shared OHLC/config fixture. This file ensures the TESTER side independently
sees the same diagnostics-array invariants when run in the TESTER test
collection.

Fixtures: imported from donor/supertrend_optimizer/testing/fixtures.py
(single source of truth — drift here trips both branches at once).
"""

from __future__ import annotations

import numpy as np
import pytest

from supertrend_optimizer.core.zigzag_st_filter import apply
from supertrend_optimizer.utils.exceptions import ConfigError

from supertrend_optimizer.testing.fixtures import (
    IMM_THRESHOLD_T,
    ImmFilterCfgDouble,
    ImmLifecycleDouble,
    imm_scenario_threshold_no_flip,
    imm_scenario_threshold_with_flip,
    make_imm_per_bar,
    make_imm_stats,
)


def _run(scenario):
    trend, per_bar, cfg, stats, dr = scenario
    return apply(
        trend=trend,
        trade_mode="both",
        trade_filter_config=cfg,
        zigzag_global_stats=stats,
        per_bar=per_bar,
        daily_reset_event=dr,
    )


# ===========================================================================
# Tester-side invariants on shared fixture (subset of §10.2)
# ===========================================================================

class TestImmediateOffSharedFixtureTester:
    """Tester branch sees the same threshold-bar contract as wf_grid."""

    def test_immediate_off_triggered_at_t(self):
        result = _run(imm_scenario_threshold_no_flip(immediate_off=True))
        arr = np.asarray(result.filter_diagnostics["exit_b_immediate_off_triggered"])
        assert arr[IMM_THRESHOLD_T] == 1

    def test_immediate_off_config_broadcast(self):
        result = _run(imm_scenario_threshold_no_flip(immediate_off=True))
        arr = np.asarray(result.filter_diagnostics["exit_b_immediate_off_config"])
        assert (arr == 1).all()

    def test_legacy_zeros_in_new_arrays(self):
        result = _run(imm_scenario_threshold_no_flip(immediate_off=False))
        diag = result.filter_diagnostics
        assert (np.asarray(diag["exit_b_immediate_off_triggered"]) == 0).all()
        assert (np.asarray(diag["exit_b_immediate_off_config"]) == 0).all()

    def test_state_off_in_immediate_off(self):
        result = _run(imm_scenario_threshold_no_flip(immediate_off=True))
        assert (
            result.filter_diagnostics["trade_filter_state"][IMM_THRESHOLD_T] == "OFF"
        )

    def test_position_closed_at_t_plus_1(self):
        result = _run(imm_scenario_threshold_no_flip(immediate_off=True))
        positions = np.asarray(result.positions)
        assert positions[IMM_THRESHOLD_T + 1] == 0


# ===========================================================================
# §10.4 dtype contract — donor TESTER side
# ===========================================================================

class TestImmediateOffDtypeContractTester:
    """§10.4: int8 dtype + always-present invariant on the tester side."""

    def test_dtype_int8_immediate_off_true(self):
        result = _run(imm_scenario_threshold_no_flip(immediate_off=True))
        diag = result.filter_diagnostics
        assert diag["exit_b_immediate_off_triggered"].dtype == np.int8
        assert diag["exit_b_immediate_off_config"].dtype == np.int8

    def test_dtype_int8_immediate_off_false(self):
        result = _run(imm_scenario_threshold_no_flip(immediate_off=False))
        diag = result.filter_diagnostics
        assert diag["exit_b_immediate_off_triggered"].dtype == np.int8
        assert diag["exit_b_immediate_off_config"].dtype == np.int8

    def test_keys_always_present(self):
        for imm in (True, False):
            result = _run(imm_scenario_threshold_no_flip(immediate_off=imm))
            diag = result.filter_diagnostics
            assert "exit_b_immediate_off_triggered" in diag
            assert "exit_b_immediate_off_config" in diag

    def test_zz_leg_stop_triggered_count_parity(self):
        r_imm = _run(imm_scenario_threshold_no_flip(immediate_off=True))
        r_legacy = _run(imm_scenario_threshold_no_flip(immediate_off=False))
        c_imm = int(
            np.asarray(r_imm.filter_diagnostics["zz_leg_stop_triggered"]).sum()
        )
        c_legacy = int(
            np.asarray(r_legacy.filter_diagnostics["zz_leg_stop_triggered"]).sum()
        )
        assert c_imm == c_legacy


# ===========================================================================
# §10.2.E: Runtime fail-fast (tester side)
# ===========================================================================

class TestImmediateOffRuntimeFailFastTester:

    def _per_bar(self, n=5):
        return make_imm_per_bar(n=n)

    def test_string_value_rejected(self):
        cfg = ImmFilterCfgDouble(
            lifecycle=ImmLifecycleDouble(
                exit_off_mode="exit B",
                exit_off_zz_leg_count=2,
                exit_b_immediate_off="yes",
            )
        )
        with pytest.raises(ConfigError, match="exit_b_immediate_off must be bool"):
            apply(
                trend=np.zeros(5, dtype=np.int64),
                trade_mode="both",
                trade_filter_config=cfg,
                zigzag_global_stats=make_imm_stats(),
                per_bar=self._per_bar(),
                daily_reset_event=np.zeros(5, dtype=bool),
            )

    def test_true_with_exit_a_rejected(self):
        cfg = ImmFilterCfgDouble(
            lifecycle=ImmLifecycleDouble(
                exit_off_mode="exit A",
                exit_off_zz_leg_count=None,
                exit_b_immediate_off=True,
            )
        )
        with pytest.raises(
            ConfigError,
            match="exit_b_immediate_off must be False",
        ):
            apply(
                trend=np.zeros(5, dtype=np.int64),
                trade_mode="both",
                trade_filter_config=cfg,
                zigzag_global_stats=make_imm_stats(),
                per_bar=self._per_bar(),
                daily_reset_event=np.zeros(5, dtype=bool),
            )

    def test_int_zero_rejected(self):
        cfg = ImmFilterCfgDouble(
            lifecycle=ImmLifecycleDouble(
                exit_off_mode="exit B",
                exit_off_zz_leg_count=2,
                exit_b_immediate_off=0,  # int, not bool
            )
        )
        with pytest.raises(ConfigError, match="exit_b_immediate_off must be bool"):
            apply(
                trend=np.zeros(5, dtype=np.int64),
                trade_mode="both",
                trade_filter_config=cfg,
                zigzag_global_stats=make_imm_stats(),
                per_bar=self._per_bar(),
                daily_reset_event=np.zeros(5, dtype=bool),
            )


# ===========================================================================
# §10.2.F vs §10.2.H: filter_block_reason contrast (tester side)
# ===========================================================================

class TestFilterBlockReasonContrastTester:

    def test_immediate_off_with_flip_filter_off(self):
        result = _run(imm_scenario_threshold_with_flip(immediate_off=True))
        assert (
            result.filter_diagnostics["filter_block_reason"][IMM_THRESHOLD_T]
            == "filter_off"
        )

    def test_legacy_with_flip_stopping_mode(self):
        result = _run(imm_scenario_threshold_with_flip(immediate_off=False))
        assert (
            result.filter_diagnostics["filter_block_reason"][IMM_THRESHOLD_T]
            == "stopping_mode_no_new_entries"
        )
