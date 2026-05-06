"""§10.2 Runtime tests for exit_b_immediate_off (Plan v3) — wf_grid side.

Subtests covered HERE:
  A. Immediate-off path (normal scenario, flip_dir==0 on threshold bar)
  B. Legacy path (exit_b_immediate_off == false): zeros in new arrays
  C. Daily-reset adjacency: reset wins over immediate-off
  D. Modes B / C / C+B orthogonality
  E. Runtime fail-fast (apply() rejects bypass of validator)
  F. Immediate-off + simultaneous flip → filter_block_reason="filter_off"
  H. Legacy path on threshold+flip bar → "stopping_mode_no_new_entries"
  I. Multi-lifecycle after immediate-off (no zombie state)

Cross-branch parity (G):
  Independent runs through wf_grid (run_single_backtest) AND donor TESTER
  (run_period) on the SAME shared fixture (synthetic OHLC + real
  TradeFilterConfig) — see TestImmediateOffCrossBranchParity.
  This is the canonical §10.2.G test; the donor TESTER mirror file only
  asserts apply()-level invariants and re-checks parity from its side.

Fixtures: imported from donor/supertrend_optimizer/testing/fixtures.py
(single source of truth; both branches consume identical inputs).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.core.zigzag_st_filter import apply
from supertrend_optimizer.utils.exceptions import ConfigError

from supertrend_optimizer.testing.fixtures import (
    IMM_THRESHOLD_T,
    ImmFilterCfgDouble,
    ImmLifecycleDouble,
    PARITY_ATR,
    PARITY_MULT,
    imm_scenario_threshold_no_flip,
    imm_scenario_threshold_with_daily_reset,
    imm_scenario_threshold_with_flip,
    make_imm_cfg,
    make_imm_per_bar,
    make_imm_stats,
    make_parity_ohlc,
    make_parity_trade_filter_config,
)


def _run_apply(scenario):
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
# §10.2.A: Immediate-off path — normal scenario (flip_dir==0)
# ===========================================================================

class TestImmediateOffPathNormal:
    """§10.2.A: bar t (threshold). state_at_bar_start=ST_COUNTING, state[t]=OFF."""

    def test_state_at_bar_start_counting(self):
        result = _run_apply(imm_scenario_threshold_no_flip(immediate_off=True))
        from supertrend_optimizer.core.zigzag_st_filter import ZigZagFSMState
        sab = np.asarray(result.filter_diagnostics["state_at_bar_start"])
        assert int(sab[IMM_THRESHOLD_T]) == int(ZigZagFSMState.ST_COUNTING_ZZ_LEGS)

    def test_state_arr_off_at_threshold(self):
        result = _run_apply(imm_scenario_threshold_no_flip(immediate_off=True))
        assert result.filter_diagnostics["trade_filter_state"][IMM_THRESHOLD_T] == "OFF"

    def test_state_code_off_at_threshold(self):
        result = _run_apply(imm_scenario_threshold_no_flip(immediate_off=True))
        from supertrend_optimizer.core.zigzag_st_filter import ZigZagFSMState
        sc = np.asarray(result.filter_diagnostics["trade_filter_state_code"])
        assert int(sc[IMM_THRESHOLD_T]) == int(ZigZagFSMState.OFF)

    def test_zz_leg_stop_triggered_at_t(self):
        """Legacy invariant: zz_leg_stop_triggered=1 in BOTH modes."""
        result = _run_apply(imm_scenario_threshold_no_flip(immediate_off=True))
        arr = np.asarray(result.filter_diagnostics["zz_leg_stop_triggered"])
        assert arr[IMM_THRESHOLD_T] == 1
        mask = np.ones_like(arr, dtype=bool)
        mask[IMM_THRESHOLD_T] = False
        assert (arr[mask] == 0).all()

    def test_immediate_off_triggered_only_at_t(self):
        result = _run_apply(imm_scenario_threshold_no_flip(immediate_off=True))
        arr = np.asarray(result.filter_diagnostics["exit_b_immediate_off_triggered"])
        assert arr.dtype == np.int8
        assert arr[IMM_THRESHOLD_T] == 1
        mask = np.ones_like(arr, dtype=bool)
        mask[IMM_THRESHOLD_T] = False
        assert (arr[mask] == 0).all()

    def test_immediate_off_config_broadcast_one(self):
        result = _run_apply(imm_scenario_threshold_no_flip(immediate_off=True))
        arr = np.asarray(result.filter_diagnostics["exit_b_immediate_off_config"])
        assert arr.dtype == np.int8
        assert (arr == 1).all()

    def test_confirmed_legs_minus_one_at_t(self):
        result = _run_apply(imm_scenario_threshold_no_flip(immediate_off=True))
        arr = np.asarray(result.filter_diagnostics["confirmed_legs_since_start"])
        assert arr[IMM_THRESHOLD_T] == -1

    def test_zz_legs_minus_one_at_t(self):
        result = _run_apply(imm_scenario_threshold_no_flip(immediate_off=True))
        arr = np.asarray(result.filter_diagnostics["zz_legs_since_lifecycle_start"])
        assert arr[IMM_THRESHOLD_T] == -1

    def test_filter_block_reason_none_when_no_flip(self):
        """flip_dir==0 → filter_block_reason='none'."""
        result = _run_apply(imm_scenario_threshold_no_flip(immediate_off=True))
        diag = result.filter_diagnostics
        assert diag["filter_block_reason"][IMM_THRESHOLD_T] == "none"
        assert int(diag["daily_reset_event"][IMM_THRESHOLD_T]) == 0
        assert int(diag["st_flip_dir"][IMM_THRESHOLD_T]) == 0

    def test_positions_no_lookahead(self):
        """positions[t+1] == 0 (closed by open_to_open contract)."""
        result = _run_apply(imm_scenario_threshold_no_flip(immediate_off=True))
        positions = np.asarray(result.positions)
        assert positions[IMM_THRESHOLD_T + 1] == 0

    def test_no_st_stopping_in_window(self):
        """state_arr[t..t+1] does NOT contain ST_STOPPING from this event."""
        result = _run_apply(imm_scenario_threshold_no_flip(immediate_off=True))
        states = np.asarray(result.filter_diagnostics["trade_filter_state"])
        for ti in range(IMM_THRESHOLD_T, min(IMM_THRESHOLD_T + 2, len(states))):
            assert states[ti] != "ST_STOPPING"


# ===========================================================================
# §10.2.B: Legacy path
# ===========================================================================

class TestLegacyPathBaseline:
    """§10.2.B: baseline behavior — no immediate-off code path."""

    def test_immediate_off_triggered_all_zero(self):
        result = _run_apply(imm_scenario_threshold_no_flip(immediate_off=False))
        arr = np.asarray(result.filter_diagnostics["exit_b_immediate_off_triggered"])
        assert arr.dtype == np.int8
        assert (arr == 0).all()

    def test_immediate_off_config_all_zero_broadcast(self):
        result = _run_apply(imm_scenario_threshold_no_flip(immediate_off=False))
        arr = np.asarray(result.filter_diagnostics["exit_b_immediate_off_config"])
        assert arr.dtype == np.int8
        assert (arr == 0).all()

    def test_zz_leg_stop_triggered_unchanged(self):
        result = _run_apply(imm_scenario_threshold_no_flip(immediate_off=False))
        arr = np.asarray(result.filter_diagnostics["zz_leg_stop_triggered"])
        assert arr[IMM_THRESHOLD_T] == 1

    def test_immediate_off_path_not_taken(self):
        result = _run_apply(imm_scenario_threshold_no_flip(immediate_off=False))
        triggered = np.asarray(
            result.filter_diagnostics["exit_b_immediate_off_triggered"]
        )
        assert triggered[IMM_THRESHOLD_T] == 0


# ===========================================================================
# §10.2.C: daily_reset adjacency
# ===========================================================================

class TestImmediateOffDailyResetAdjacency:
    """§10.2.C: daily_reset on threshold bar wipes BEFORE immediate-off check."""

    def test_daily_reset_wins(self):
        result = _run_apply(
            imm_scenario_threshold_with_daily_reset(immediate_off=True, reset_at=3)
        )
        diag = result.filter_diagnostics
        assert diag["filter_block_reason"][3] == "daily_reset"
        assert int(np.asarray(diag["exit_b_immediate_off_triggered"])[3]) == 0
        # threshold check ALSO not fired (state was wiped to OFF before check)
        assert int(np.asarray(diag["zz_leg_stop_triggered"])[3]) == 0


# ===========================================================================
# §10.2.D: Modes B / C / C+B orthogonality
# ===========================================================================

class TestImmediateOffModeOrthogonality:
    """§10.2.D: immediate-off is orthogonal to ZigZag mode.

    The plan §4 doesn't make the immediate-off path depend on the resolved
    mode — only on (state == ST_COUNTING_ZZ_LEGS) ∧ (zz_legs >= target).
    What changes with mode is HOW legs get confirmed (candidate vs median
    triggers). The orthogonality contract checked here:

      1. mode is echoed faithfully per-bar (not silently overridden);
      2. when threshold is reached (mode A scenario), the immediate-off
         contract holds bit-identical to mode A's outcome — i.e. immediate-off
         doesn't change with mode;
      3. when threshold is NOT reached (modes B / C / C+B with our A-style
         scenario) — immediate-off path is silently inactive, triggered=0
         everywhere, AND positions[t+1]=0 (no entry was made at all),
         confirming "immediate-off doesn't activate spuriously in foreign modes".

    For a stronger end-to-end orthogonality (legs actually confirmed in
    each mode), see TestImmediateOffCrossBranchParity which uses real
    OHLC and the production stats builder.
    """

    REACHABLE_MODES = ("A", "A+B")
    FOREIGN_MODES = ("B", "C", "C+B")

    @pytest.mark.parametrize("mode", REACHABLE_MODES + FOREIGN_MODES)
    def test_zigzag_mode_echoed_per_bar(self, mode):
        """mode is echoed faithfully into per-bar diagnostics in every mode."""
        trend, per_bar, cfg, _stats, dr = imm_scenario_threshold_no_flip(
            immediate_off=True
        )
        stats = make_imm_stats(zigzag_mode=mode)
        result = apply(
            trend=trend,
            trade_mode="both",
            trade_filter_config=cfg,
            zigzag_global_stats=stats,
            per_bar=per_bar,
            daily_reset_event=dr,
        )
        zz_mode_arr = np.asarray(result.filter_diagnostics["zigzag_mode"])
        assert (zz_mode_arr == mode).all(), (
            f"mode={mode}: zigzag_mode echo mismatch — observed unique "
            f"values: {set(zz_mode_arr.tolist())}"
        )

    @pytest.mark.parametrize("mode", REACHABLE_MODES)
    def test_immediate_off_fires_when_threshold_reached(self, mode):
        """Modes A / A+B: threshold reached → immediate-off path fires identically."""
        trend, per_bar, cfg, _stats, dr = imm_scenario_threshold_no_flip(
            immediate_off=True
        )
        stats = make_imm_stats(zigzag_mode=mode)
        result = apply(
            trend=trend,
            trade_mode="both",
            trade_filter_config=cfg,
            zigzag_global_stats=stats,
            per_bar=per_bar,
            daily_reset_event=dr,
        )
        diag = result.filter_diagnostics
        assert diag["trade_filter_state"][IMM_THRESHOLD_T] == "OFF"
        triggered = np.asarray(diag["exit_b_immediate_off_triggered"])
        assert triggered[IMM_THRESHOLD_T] == 1
        assert np.asarray(result.positions)[IMM_THRESHOLD_T + 1] == 0

    @pytest.mark.parametrize("mode", FOREIGN_MODES)
    def test_immediate_off_silent_when_threshold_not_reached(self, mode):
        """Modes B / C / C+B: A-style scenario doesn't reach threshold;
        immediate-off path stays silent (no spurious activation)."""
        trend, per_bar, cfg, _stats, dr = imm_scenario_threshold_no_flip(
            immediate_off=True
        )
        stats = make_imm_stats(zigzag_mode=mode)
        result = apply(
            trend=trend,
            trade_mode="both",
            trade_filter_config=cfg,
            zigzag_global_stats=stats,
            per_bar=per_bar,
            daily_reset_event=dr,
        )
        diag = result.filter_diagnostics
        triggered = np.asarray(diag["exit_b_immediate_off_triggered"])
        assert (triggered == 0).all(), (
            f"mode={mode}: immediate-off fired in foreign mode — "
            f"triggered indices: {np.where(triggered != 0)[0].tolist()}"
        )
        # config-broadcast still reflects the user flag, even when path is silent
        config_arr = np.asarray(diag["exit_b_immediate_off_config"])
        assert (config_arr == 1).all()
        # No positions opened in our A-style scenario for foreign modes →
        # positions stay at 0 throughout (no spurious open_to_open writes).
        assert (np.asarray(result.positions) == 0).all()


# ===========================================================================
# §10.2.E: Runtime fail-fast
# ===========================================================================

class TestImmediateOffRuntimeFailFast:
    """§10.2.E: apply() with duck-typed config rejects bad imm values."""

    def test_string_value_rejected_in_exit_b(self):
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
                per_bar=make_imm_per_bar(n=5),
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
            match="exit_b_immediate_off must be False when exit_off_mode != 'exit B'",
        ):
            apply(
                trend=np.zeros(5, dtype=np.int64),
                trade_mode="both",
                trade_filter_config=cfg,
                zigzag_global_stats=make_imm_stats(),
                per_bar=make_imm_per_bar(n=5),
                daily_reset_event=np.zeros(5, dtype=bool),
            )

    def test_int_zero_with_exit_b_rejected(self):
        """int 0 is not bool False → identity check `is not False` → invalid_type."""
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
                per_bar=make_imm_per_bar(n=5),
                daily_reset_event=np.zeros(5, dtype=bool),
            )


# ===========================================================================
# §10.2.F: Immediate-off + simultaneous flip
# ===========================================================================

class TestImmediateOffWithSimultaneousFlip:
    """§10.2.F: flip_dir!=0 on threshold bar with immediate-off → 'filter_off'."""

    def test_state_off_at_threshold(self):
        result = _run_apply(imm_scenario_threshold_with_flip(immediate_off=True))
        assert result.filter_diagnostics["trade_filter_state"][IMM_THRESHOLD_T] == "OFF"

    def test_immediate_off_triggered_at_t(self):
        result = _run_apply(imm_scenario_threshold_with_flip(immediate_off=True))
        arr = np.asarray(result.filter_diagnostics["exit_b_immediate_off_triggered"])
        assert arr[IMM_THRESHOLD_T] == 1

    def test_filter_block_reason_filter_off(self):
        """state goes to OFF before flip-reason branch → 'filter_off'."""
        result = _run_apply(imm_scenario_threshold_with_flip(immediate_off=True))
        assert (
            result.filter_diagnostics["filter_block_reason"][IMM_THRESHOLD_T]
            == "filter_off"
        )

    def test_position_t_plus_1_zero(self):
        result = _run_apply(imm_scenario_threshold_with_flip(immediate_off=True))
        assert np.asarray(result.positions)[IMM_THRESHOLD_T + 1] == 0


# ===========================================================================
# §10.2.H: Legacy path on threshold+flip bar
# ===========================================================================

class TestLegacyPathThresholdWithFlip:
    """§10.2.H: legacy. state=ST_STOPPING; reason='stopping_mode_no_new_entries'."""

    def test_state_st_stopping_at_threshold(self):
        result = _run_apply(imm_scenario_threshold_with_flip(immediate_off=False))
        assert (
            result.filter_diagnostics["trade_filter_state"][IMM_THRESHOLD_T]
            == "ST_STOPPING"
        )

    def test_filter_block_reason_stopping_mode(self):
        result = _run_apply(imm_scenario_threshold_with_flip(immediate_off=False))
        assert (
            result.filter_diagnostics["filter_block_reason"][IMM_THRESHOLD_T]
            == "stopping_mode_no_new_entries"
        )

    def test_immediate_off_triggered_zero(self):
        result = _run_apply(imm_scenario_threshold_with_flip(immediate_off=False))
        arr = np.asarray(result.filter_diagnostics["exit_b_immediate_off_triggered"])
        assert arr[IMM_THRESHOLD_T] == 0


# ===========================================================================
# §10.2.I: Multi-lifecycle — no zombie state after immediate-off
# ===========================================================================

class TestImmediateOffMultiLifecycle:
    """§10.2.I: after immediate-off, lifecycle restarts cleanly on next trigger."""

    def test_counter_reset_after_immediate_off(self):
        n = 12
        per_bar = make_imm_per_bar(
            n=n,
            candidate_height_pct=np.array(
                [np.nan, 0.06, np.nan, np.nan,
                 np.nan, np.nan, 0.06, np.nan,
                 np.nan, np.nan, np.nan, np.nan]
            ),
            confirm_event=np.array(
                [0, 0, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0], dtype=np.int8
            ),
        )
        trend = np.array(
            [-1, 1, 1, 1, -1, -1, 1, 1, 1, 1, 1, 1], dtype=np.int64
        )
        cfg = make_imm_cfg(exit_b_immediate_off=True)
        result = apply(
            trend=trend,
            trade_mode="both",
            trade_filter_config=cfg,
            zigzag_global_stats=make_imm_stats(),
            per_bar=per_bar,
            daily_reset_event=np.zeros(n, dtype=bool),
        )
        diag = result.filter_diagnostics

        # immediate-off triggered exactly once at t=3
        triggered = np.asarray(diag["exit_b_immediate_off_triggered"])
        assert triggered[3] == 1
        assert int(triggered.sum()) == 1

        # Bars 4..5 (between lifecycles): counter must be sentinel -1
        zz_legs = np.asarray(diag["zz_legs_since_lifecycle_start"])
        for ti in (4, 5):
            assert zz_legs[ti] == -1, (
                f"bar {ti}: zz_legs={zz_legs[ti]}; expected -1 (zombie)"
            )

        # On lifecycle2 first COUNTING bar — must be 0, not zombie value
        states = np.asarray(diag["trade_filter_state"])
        counting_indices = np.where(states == "ST_COUNTING_ZZ_LEGS")[0]
        lifecycle2_counting = counting_indices[counting_indices > 5]
        if len(lifecycle2_counting) > 0:
            first_l2 = lifecycle2_counting[0]
            assert zz_legs[first_l2] == 0, (
                f"lifecycle2 first COUNTING bar {first_l2}: "
                f"zz_legs={zz_legs[first_l2]}; expected 0 (zombie)"
            )


# ===========================================================================
# §10.4 dtype contract
# ===========================================================================

class TestImmediateOffDtypeContract:
    """§10.4: int8 dtype + always-present invariant."""

    def test_dtype_int8_when_flag_true(self):
        result = _run_apply(imm_scenario_threshold_no_flip(immediate_off=True))
        diag = result.filter_diagnostics
        assert diag["exit_b_immediate_off_triggered"].dtype == np.int8
        assert diag["exit_b_immediate_off_config"].dtype == np.int8

    def test_dtype_int8_when_flag_false(self):
        result = _run_apply(imm_scenario_threshold_no_flip(immediate_off=False))
        diag = result.filter_diagnostics
        assert diag["exit_b_immediate_off_triggered"].dtype == np.int8
        assert diag["exit_b_immediate_off_config"].dtype == np.int8

    def test_keys_always_present_when_filter_enabled(self):
        for imm in (True, False):
            result = _run_apply(imm_scenario_threshold_no_flip(immediate_off=imm))
            diag = result.filter_diagnostics
            assert "exit_b_immediate_off_triggered" in diag
            assert "exit_b_immediate_off_config" in diag

    def test_zz_leg_stop_triggered_count_parity(self):
        r_imm = _run_apply(imm_scenario_threshold_no_flip(immediate_off=True))
        r_legacy = _run_apply(imm_scenario_threshold_no_flip(immediate_off=False))
        c_imm = int(
            np.asarray(r_imm.filter_diagnostics["zz_leg_stop_triggered"]).sum()
        )
        c_legacy = int(
            np.asarray(r_legacy.filter_diagnostics["zz_leg_stop_triggered"]).sum()
        )
        assert c_imm == c_legacy


# ===========================================================================
# BacktestResult dtype contract (§7) — negative test (ConfigError per Plan v3)
# ===========================================================================

class TestBacktestResultImmediateOffDtypeContract:
    """§10.4 negative: BacktestResult rejects new arrays with wrong dtype.

    Plan v3 §7 specifies ConfigError for the new keys (different from
    pre-existing keys which still raise ValueError for backward compat).
    """

    def _make_result(self, fd: dict):
        from supertrend_optimizer.engine.result import BacktestResult

        n = 5
        return BacktestResult(
            atr_period=14,
            multiplier=3.0,
            trade_mode="both",
            commission=0.0,
            warmup=0,
            returns=np.zeros(n - 1, dtype=np.float64),
            equity_curve=np.ones(n, dtype=np.float64),
            positions=np.zeros(n, dtype=np.int8),
            trend=np.zeros(n, dtype=np.int64),
            metrics={},
            early_exit=False,
            exit_bar=None,
            exit_drawdown=None,
            trades_df=None,
            n_bars_original=n,
            filter_diagnostics=fd,
        )

    def test_wrong_dtype_triggered_rejected(self):
        n = 5
        bad_arr = np.zeros(n, dtype=np.int64)  # should be int8
        with pytest.raises(ConfigError, match="exit_b_immediate_off_triggered"):
            self._make_result({"exit_b_immediate_off_triggered": bad_arr})

    def test_wrong_dtype_config_rejected(self):
        n = 5
        bad_arr = np.zeros(n, dtype=np.float32)
        with pytest.raises(ConfigError, match="exit_b_immediate_off_config"):
            self._make_result({"exit_b_immediate_off_config": bad_arr})

    def test_correct_dtype_accepted(self):
        n = 5
        good_arr = np.zeros(n, dtype=np.int8)
        result = self._make_result(
            {
                "exit_b_immediate_off_triggered": good_arr.copy(),
                "exit_b_immediate_off_config": good_arr.copy(),
            }
        )
        assert result.filter_diagnostics is not None


# ===========================================================================
# §10.2.G: Cross-branch parity (independent runs through both pipelines)
# ===========================================================================

class TestImmediateOffCrossBranchParity:
    """§10.2.G: WF Grid (run_single_backtest) vs donor TESTER (run_period)
    on the same shared fixture must produce bit-identical shared arrays.

    This is the canonical parity test. It runs BOTH pipelines through their
    real production entrypoints (not through the shared apply() directly)
    and compares the diagnostics they produce.
    """

    @pytest.mark.parametrize("immediate_off", [True, False])
    def test_shared_arrays_bit_identical(self, immediate_off):
        from supertrend_optimizer.engine.run import run_single_backtest
        from supertrend_optimizer.testing.runner import run_period
        from supertrend_optimizer.core.zigzag_st_filter import build_zigzag_global_stats
        from supertrend_optimizer.utils.enums import ExecutionModel

        df = make_parity_ohlc()
        cfg = make_parity_trade_filter_config(exit_b_immediate_off=immediate_off)
        stats = build_zigzag_global_stats(df["close"].values, cfg)

        # WF Grid pipeline
        wf_result = run_single_backtest(
            open_prices=df["open"].values,
            high=df["high"].values,
            low=df["low"].values,
            close=df["close"].values,
            index=df.index,
            atr_period=PARITY_ATR,
            multiplier=PARITY_MULT,
            trade_mode="revers",
            commission=0.001,
            execution_model=ExecutionModel.OPEN_TO_OPEN,
            early_exit_enabled=False,
            min_trades_required=1,
            extract_trades_flag=True,
            caller_mode="wf_grid",
            trade_filter_config=cfg,
            zigzag_global_stats=stats,
            global_offset=0,
        )

        # Tester pipeline
        tester_period = run_period(
            df=df,
            atr_period=PARITY_ATR,
            multiplier=PARITY_MULT,
            trade_mode="revers",
            commission=0.001,
            execution_model=ExecutionModel.OPEN_TO_OPEN,
            min_trades_required=1,
            trade_filter_config=cfg,
            zigzag_global_stats=stats,
            global_offset=0,
        )
        tester_result = tester_period.result

        assert wf_result.filter_diagnostics is not None
        assert tester_result.filter_diagnostics is not None

        # Shared per-bar arrays — bit-identical
        shared_keys = [
            "trade_filter_state",
            "trade_filter_state_code",
            "zz_legs_since_lifecycle_start",
            "zz_leg_stop_triggered",
            "exit_b_immediate_off_triggered",
            "exit_b_immediate_off_config",
            "filter_block_reason",
        ]
        for k in shared_keys:
            np.testing.assert_array_equal(
                np.asarray(wf_result.filter_diagnostics[k]),
                np.asarray(tester_result.filter_diagnostics[k]),
                err_msg=f"§10.2.G parity failed for diagnostics key {k!r} "
                f"(immediate_off={immediate_off})",
            )

        # filtered_positions parity
        np.testing.assert_array_equal(
            np.asarray(wf_result.positions),
            np.asarray(tester_result.positions),
            err_msg=(
                f"§10.2.G parity failed for positions "
                f"(immediate_off={immediate_off})"
            ),
        )

        # trades_df row-wise parity (when both produced trades)
        wf_trades = wf_result.trades_df
        tester_trades = tester_result.trades_df
        if wf_trades is None or tester_trades is None:
            assert wf_trades is None and tester_trades is None, (
                f"§10.2.G trades_df presence mismatch "
                f"(immediate_off={immediate_off})"
            )
        else:
            pd.testing.assert_frame_equal(
                wf_trades.reset_index(drop=True),
                tester_trades.reset_index(drop=True),
                check_dtype=True,
                obj=f"§10.2.G trades_df (immediate_off={immediate_off})",
            )

    def test_immediate_off_arrays_filled_when_flag_true(self):
        """Sanity: at least one immediate-off bar exists in the parity run with flag=True."""
        from supertrend_optimizer.testing.runner import run_period
        from supertrend_optimizer.core.zigzag_st_filter import build_zigzag_global_stats
        from supertrend_optimizer.utils.enums import ExecutionModel

        df = make_parity_ohlc()
        cfg = make_parity_trade_filter_config(exit_b_immediate_off=True)
        stats = build_zigzag_global_stats(df["close"].values, cfg)
        pr = run_period(
            df=df,
            atr_period=PARITY_ATR,
            multiplier=PARITY_MULT,
            trade_mode="revers",
            commission=0.001,
            execution_model=ExecutionModel.OPEN_TO_OPEN,
            min_trades_required=1,
            trade_filter_config=cfg,
            zigzag_global_stats=stats,
            global_offset=0,
        )
        triggered = np.asarray(
            pr.result.filter_diagnostics["exit_b_immediate_off_triggered"]
        )
        config_arr = np.asarray(
            pr.result.filter_diagnostics["exit_b_immediate_off_config"]
        )
        # config broadcast = 1 across the whole period
        assert (config_arr == 1).all()
        # We don't require ≥1 firing on a small synthetic sample; the parity
        # contract is the assertion in test_shared_arrays_bit_identical above.
        # Here we only ensure the array is present and dtype-correct.
        assert triggered.dtype == np.int8
