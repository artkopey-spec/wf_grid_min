"""
WP11 unit tests — Rollout and rollback hardening for ZigZag ST trade_filter.

Plan reference:  WP11 (plan §WP11, §14, §17.1.1).
Spec reference:  Appendix A v1.1 §11, §11.1, §17.1, §18.

Coverage areas
--------------
R1. Rollback contract: trade_filter absent = enabled=false = baseline bit-identical
    (positions, returns, equity, trades, metrics).
R2. Config rollback: pipeline produced by enabled config can be reproduced by
    flipping trade_filter.enabled=false (no enabled-path contamination).
R3. Enabled-path does NOT pollute disabled-path artifacts:
    - no filter_diagnostics when disabled;
    - no entry_filter_state / entry_trigger_source / exit_reason in trades;
    - no summary columns populated when disabled.
R4. Anti-drift gate: no stale 7-tuple unpack of run_backtest_fast in active scope.
R5. Anti-drift gate: apply accepts high/low for ATR; ZigZag remains close-only.
R6. Anti-drift gate: no legacy trigger_source key in filter_diagnostics.
R7. Anti-drift gate: no module-level mutable state in zigzag_st_filter (FSM).
R8. Force-flat does not modify filter_diagnostics (WP8 contract re-confirmed).
R9. StepResult defensive_fallback produces filter_diagnostics_oos=None,
    filter_diagnostics_summary=None.
R10. config.yaml production config has trade_filter.enabled=false (safe default).

Anti-drift (WP11)
-----------------
- No new FSM logic, no new config fields.
- No changes to enabled-path semantics.
- Minimal: only rollback/parity/anti-drift confirmations.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.core.backtest import run_backtest_fast
from supertrend_optimizer.core.backtest import generate_positions
from supertrend_optimizer.core.calculator import calculate_supertrend
from supertrend_optimizer.core.zigzag_st_filter import apply as zigzag_apply
from supertrend_optimizer.engine.run import run_single_backtest
from supertrend_optimizer.utils.enums import ExecutionModel

from wf_grid.collect.trades_collector import _FILTER_TRADE_COLS


# ===========================================================================
# Shared helpers / test doubles
# ===========================================================================

_PROJECT_ROOT = Path(__file__).parent.parent.parent


def _make_prices(n: int = 100, seed: int = 42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.cumsum(rng.normal(0, 0.5, n)) + 100.0


def _ohlc(close: np.ndarray):
    open_p = np.roll(close, 1)
    open_p[0] = close[0]
    high = close + 0.5
    low = close - 0.5
    return open_p, high, low, close


@dataclass
class _Toggle:
    enabled: bool = True


@dataclass
class _Triggers:
    candidate_threshold: _Toggle = field(default_factory=_Toggle)
    confirmed_median: _Toggle = field(
        default_factory=lambda: _Toggle(enabled=False)
    )


@dataclass
class _Lifecycle:
    freeze_confirmed_legs: int = 3
    stop_check: str = "confirm_bar_only"
    stopping_exit: str = "opposite_st_flip"


@dataclass
class _ZigZagCfg:
    reversal_threshold: float = 0.02
    local_window: int = 5
    global_stats_source: str = "full_dataset"
    leg_height_mode: str = "pct"
    global_median: str = "auto"
    candidate_trigger_threshold: float = 0.01
    candidate_trigger_quantile: Optional[float] = None


@dataclass
class _FilterCfg:
    enabled: bool = True
    type: str = "zigzag_st_mode"
    zigzag: _ZigZagCfg = field(default_factory=_ZigZagCfg)
    triggers: _Triggers = field(default_factory=_Triggers)
    lifecycle: _Lifecycle = field(default_factory=_Lifecycle)


def _disabled_cfg() -> _FilterCfg:
    c = _FilterCfg()
    c.enabled = False
    return c


def _make_global_stats():
    from supertrend_optimizer.core.zigzag_st_filter import ZigZagGlobalStats
    return ZigZagGlobalStats(
        reversal_threshold=0.02,
        global_stats_source="full_dataset",
        leg_height_mode="pct",
        confirmed_legs=[],
        confirmed_heights_pct=np.array([], dtype=np.float64),
        global_median=0.03,
        candidate_trigger_threshold=0.01,
        candidate_trigger_source="explicit",
        candidate_trigger_quantile=None,
        n_legs_total=0,
        insufficient_data=False,
        fail_closed_reason=None,
        metadata={},
    )


def _run_backtest(n: int = 80, *, filter_cfg=None, global_stats=None):
    close = _make_prices(n)
    o, h, l, c = _ohlc(close)
    return run_backtest_fast(
        o, h, l, c,
        atr_period=5, multiplier=2.0, trade_mode="revers",
        commission=0.001,
        early_exit_enabled=False,
        early_exit_max_drawdown=0.5,
        early_exit_check_bars=0,
        trade_filter_config=filter_cfg,
        zigzag_global_stats=global_stats,
    )


# ===========================================================================
# R1. Rollback contract: absent == disabled == baseline bit-identical
# ===========================================================================

class TestRollbackContract:
    """plan §WP11 step 1 / spec §11.1 — disabled = absent = baseline."""

    def test_absent_filter_equals_disabled_filter_positions(self):
        """trade_filter=None and trade_filter.enabled=false produce identical positions."""
        n = 80
        arts_none = _run_backtest(n, filter_cfg=None)
        arts_disabled = _run_backtest(n, filter_cfg=_disabled_cfg(),
                                      global_stats=_make_global_stats())
        np.testing.assert_array_equal(
            arts_none.positions, arts_disabled.positions,
            err_msg="absent filter != disabled filter on positions",
        )

    def test_absent_filter_equals_disabled_filter_returns(self):
        n = 80
        arts_none = _run_backtest(n, filter_cfg=None)
        arts_disabled = _run_backtest(n, filter_cfg=_disabled_cfg(),
                                      global_stats=_make_global_stats())
        np.testing.assert_array_equal(
            arts_none.returns, arts_disabled.returns,
            err_msg="absent filter != disabled filter on returns",
        )

    def test_absent_filter_equals_generate_positions(self):
        """Disabled path uses generate_positions exactly (no post-filter contamination)."""
        n = 80
        close = _make_prices(n)
        o, h, l, c = _ohlc(close)
        arts_none = run_backtest_fast(
            o, h, l, c,
            atr_period=5, multiplier=2.0, trade_mode="revers",
            commission=0.001,
            early_exit_enabled=False,
            early_exit_max_drawdown=0.5,
            early_exit_check_bars=0,
            trade_filter_config=None,
        )
        # Reproduce via generate_positions directly
        trend_arr, _ = calculate_supertrend(h, l, c, 5, 2.0)
        expected_positions = generate_positions(
            trend_arr, "revers", execution_model=ExecutionModel.OPEN_TO_OPEN,
        )
        np.testing.assert_array_equal(
            arts_none.positions, expected_positions,
            err_msg="disabled-path positions != generate_positions baseline",
        )

    def test_disabled_filter_diagnostics_is_none(self):
        """filter_diagnostics must be None on disabled/absent path."""
        arts_none = _run_backtest(filter_cfg=None)
        arts_disabled = _run_backtest(filter_cfg=_disabled_cfg(),
                                      global_stats=_make_global_stats())
        assert arts_none.filter_diagnostics is None
        assert arts_disabled.filter_diagnostics is None


# ===========================================================================
# R2. Config rollback: flipping enabled=false restores baseline
# ===========================================================================

class TestConfigFlipRollback:
    """Flipping enabled=false from enabled=true restores bit-identical baseline."""

    def _run_single(self, n: int, filter_cfg, global_stats=None):
        close = _make_prices(n)
        o, h, l, c = _ohlc(close)
        return run_single_backtest(
            open_prices=o, high=h, low=l, close=close,
            index=pd.RangeIndex(n),
            atr_period=5, multiplier=2.0, trade_mode="revers",
            commission=0.001, warmup_period=10,
            early_exit_enabled=False,
            early_exit_max_drawdown=0.5,
            early_exit_check_bars=0,
            periods_per_year=252, min_trades_required=1,
            extract_trades_flag=True,
            auto_warmup=True,
            execution_model=ExecutionModel.OPEN_TO_OPEN,
            trade_filter_config=filter_cfg,
            zigzag_global_stats=global_stats,
        )

    def test_rollback_positions_identical(self):
        n = 80
        result_none = self._run_single(n, filter_cfg=None)
        result_disabled = self._run_single(n, filter_cfg=_disabled_cfg(),
                                            global_stats=_make_global_stats())
        np.testing.assert_array_equal(
            result_none.positions, result_disabled.positions,
            err_msg="rollback: enabled=false positions differ from absent baseline",
        )

    def test_rollback_returns_identical(self):
        n = 80
        result_none = self._run_single(n, filter_cfg=None)
        result_disabled = self._run_single(n, filter_cfg=_disabled_cfg(),
                                            global_stats=_make_global_stats())
        np.testing.assert_array_equal(
            result_none.returns, result_disabled.returns,
            err_msg="rollback: enabled=false returns differ from absent baseline",
        )

    def test_rollback_filter_diagnostics_none(self):
        n = 80
        result_disabled = self._run_single(n, filter_cfg=_disabled_cfg(),
                                            global_stats=_make_global_stats())
        assert result_disabled.filter_diagnostics is None, (
            "rollback: filter_diagnostics should be None after flip to disabled"
        )


# ===========================================================================
# R3. Enabled path does NOT contaminate disabled-path artifacts
# ===========================================================================

class TestEnabledPathNoPollution:
    """No cross-contamination between enabled and disabled paths."""

    def test_disabled_trades_no_filter_columns(self):
        """WF_Trades from disabled path must NOT contain filter trade columns."""
        n = 80
        close = _make_prices(n)
        o, h, l, c = _ohlc(close)
        result = run_single_backtest(
            open_prices=o, high=h, low=l, close=close,
            index=pd.RangeIndex(n),
            atr_period=5, multiplier=2.0, trade_mode="revers",
            commission=0.001, warmup_period=10,
            early_exit_enabled=False,
            early_exit_max_drawdown=0.5,
            early_exit_check_bars=0,
            periods_per_year=252, min_trades_required=1,
            extract_trades_flag=True, auto_warmup=True,
            execution_model=ExecutionModel.OPEN_TO_OPEN,
            trade_filter_config=_disabled_cfg(),
            zigzag_global_stats=_make_global_stats(),
        )
        if result.trades_df is not None and len(result.trades_df) > 0:
            for col in _FILTER_TRADE_COLS:
                assert col not in result.trades_df.columns, (
                    f"Filter column {col!r} must not be in trades on disabled path"
                )

    def test_disabled_summary_fields_not_populated(self):
        """Step summary fields should be None on disabled path (no filter)."""
        from wf_grid.wf.step_executor import _compute_filter_diagnostics_summary
        summary = _compute_filter_diagnostics_summary(None)
        assert summary is None


# ===========================================================================
# R4. Anti-drift: no stale 7-tuple unpack of run_backtest_fast
# ===========================================================================

class TestAntiDriftNoTupleUnpack:
    """plan §8.2 migration scope: no 7-value tuple unpack in active scope."""

    def test_run_backtest_fast_returns_dataclass_not_tuple(self):
        from supertrend_optimizer.core.backtest import RawBacktestArtifacts
        arts = _run_backtest(filter_cfg=None)
        assert isinstance(arts, RawBacktestArtifacts), (
            "run_backtest_fast must return RawBacktestArtifacts dataclass, not tuple"
        )

    def test_rawbacktestartifacts_not_iterable_as_7tuple(self):
        """RawBacktestArtifacts should NOT unpack as a 7-value tuple transparently."""
        from supertrend_optimizer.core.backtest import RawBacktestArtifacts
        arts = _run_backtest(filter_cfg=None)
        # Attempting to unpack as 7 values must fail (it's a dataclass)
        with pytest.raises(Exception):
            a, b, c, d, e, f, g = arts  # type: ignore[misc]


# ===========================================================================
# R5. Anti-drift: high/low accepted for ATR; ZigZag remains close-only
# ===========================================================================

class TestAntiDriftHighLowContractInApply:
    """plan §8.3.1 / spec §3.4 narrowed close-only invariant."""

    def test_apply_signature_accepts_optional_high_low_for_wakeup_atr(self):
        sig = inspect.signature(zigzag_apply)
        params = sig.parameters
        assert params["high"].default is None
        assert params["low"].default is None

    def test_distorted_high_low_does_not_change_apply_output(self):
        """Distorted high/low must not affect close-derived ZigZag diagnostics.

        Backtest-level positions/FSM may legitimately differ because
        SuperTrend consumes OHLC before apply().
        """
        n = 80
        close = _make_prices(n)
        o, h, l, c = _ohlc(close)

        def _run(open_p, high, low):
            return run_single_backtest(
                open_prices=open_p, high=high, low=low, close=close,
                index=pd.RangeIndex(n),
                atr_period=5, multiplier=2.0, trade_mode="revers",
                commission=0.001, warmup_period=10,
                early_exit_enabled=False,
                early_exit_max_drawdown=0.5,
                early_exit_check_bars=0,
                periods_per_year=252, min_trades_required=1,
                extract_trades_flag=True, auto_warmup=True,
                execution_model=ExecutionModel.OPEN_TO_OPEN,
                trade_filter_config=_FilterCfg(),
                zigzag_global_stats=_make_global_stats(),
            )

        result_normal = _run(o, h, l)
        # Distort high/low — SuperTrend changes, but ZigZag diagnostics must not.
        h_distorted = h * 5.0
        l_distorted = l * 0.1
        result_distorted = _run(o, h_distorted, l_distorted)

        if (result_normal.filter_diagnostics is not None
                and result_distorted.filter_diagnostics is not None):
            diag_n = result_normal.filter_diagnostics
            diag_d = result_distorted.filter_diagnostics
            # candidate_height_pct is ZigZag-derived — must be identical
            np.testing.assert_array_equal(
                diag_n["candidate_height_pct"],
                diag_d["candidate_height_pct"],
                err_msg="candidate_height_pct differs: high/low leaked into ZigZag",
            )
            np.testing.assert_array_equal(
                diag_n["local_median_N"],
                diag_d["local_median_N"],
                err_msg="local_median_N differs: high/low leaked into ZigZag",
            )


# ===========================================================================
# R6. Anti-drift: no legacy trigger_source key
# ===========================================================================

class TestAntiDriftNoLegacyKey:
    """No stale 'trigger_source' key in filter_diagnostics."""

    def test_no_legacy_trigger_source_key(self):
        n = 80
        close = _make_prices(n)
        o, h, l, c = _ohlc(close)
        result = run_single_backtest(
            open_prices=o, high=h, low=l, close=close,
            index=pd.RangeIndex(n),
            atr_period=5, multiplier=2.0, trade_mode="revers",
            commission=0.001, warmup_period=10,
            early_exit_enabled=False,
            early_exit_max_drawdown=0.5,
            early_exit_check_bars=0,
            periods_per_year=252, min_trades_required=1,
            extract_trades_flag=True, auto_warmup=True,
            execution_model=ExecutionModel.OPEN_TO_OPEN,
            trade_filter_config=_FilterCfg(),
            zigzag_global_stats=_make_global_stats(),
        )
        diag = result.filter_diagnostics
        if diag is not None:
            assert "trigger_source" not in diag, (
                "Legacy key 'trigger_source' found in filter_diagnostics; "
                "must use canonical 'trade_filter_trigger_source'"
            )
            assert "trade_filter_trigger_source" in diag, (
                "Canonical key 'trade_filter_trigger_source' missing"
            )

    def test_no_ab_values_in_trigger_source(self):
        """Trigger source values must be canonical — no 'A' or 'B'."""
        n = 80
        close = _make_prices(n)
        o, h, l, c = _ohlc(close)
        result = run_single_backtest(
            open_prices=o, high=h, low=l, close=close,
            index=pd.RangeIndex(n),
            atr_period=5, multiplier=2.0, trade_mode="revers",
            commission=0.001, warmup_period=10,
            early_exit_enabled=False,
            early_exit_max_drawdown=0.5,
            early_exit_check_bars=0,
            periods_per_year=252, min_trades_required=1,
            extract_trades_flag=True, auto_warmup=True,
            execution_model=ExecutionModel.OPEN_TO_OPEN,
            trade_filter_config=_FilterCfg(),
            zigzag_global_stats=_make_global_stats(),
        )
        diag = result.filter_diagnostics
        if diag is not None:
            arr = diag.get("trade_filter_trigger_source", np.array([]))
            allowed = {"candidate_threshold", "confirmed_median", "both", "none"}
            actual = set(str(v) for v in arr)
            forbidden = actual - allowed
            assert not forbidden, (
                f"Non-canonical trigger_source values: {forbidden!r}. "
                f"Legacy 'A'/'B' values must not appear."
            )


# ===========================================================================
# R7. Anti-drift: FSM no module-level state (rollback re-confirm)
# ===========================================================================

class TestAntiDriftFSMNoModuleState:
    """Two sequential apply() calls must give bit-identical results (§10.7.2)."""

    def test_sequential_apply_bit_identical(self):
        from supertrend_optimizer.core.zigzag_st_filter import (
            ZigZagPerBar,
            apply as zigzag_apply,
        )
        n = 60
        rng = np.random.default_rng(11)
        trend = np.where(rng.random(n) > 0.5, 1, -1).astype(np.int8)

        per_bar = ZigZagPerBar(
            candidate_height_pct=rng.uniform(0.005, 0.05, n).astype(np.float64),
            confirm_event=np.where(rng.random(n) > 0.8, 1, 0).astype(np.int8),
            local_median_N=rng.uniform(0.02, 0.06, n).astype(np.float64),
            local_median_available=np.ones(n, dtype=np.int8),
            confirmed_leg_idx_at_t=np.full(n, -1, dtype=np.int64),
            last_confirmed_leg_height_pct=np.full(n, np.nan, dtype=np.float64),
        )
        cfg = _FilterCfg()
        stats = _make_global_stats()

        r1 = zigzag_apply(trend=trend, trade_mode="revers",
                           trade_filter_config=cfg, zigzag_global_stats=stats,
                           per_bar=per_bar)
        r2 = zigzag_apply(trend=trend, trade_mode="revers",
                           trade_filter_config=cfg, zigzag_global_stats=stats,
                           per_bar=per_bar)

        np.testing.assert_array_equal(r1.positions, r2.positions,
                                       err_msg="FSM module state contamination")
        for key in r1.filter_diagnostics:
            a1, a2 = r1.filter_diagnostics[key], r2.filter_diagnostics[key]
            assert list(a1) == list(a2), f"FSM state contamination in {key!r}"


# ===========================================================================
# R8. Force-flat does not modify filter_diagnostics (WP8 re-confirm)
# ===========================================================================

class TestForceFlatDiagnosticsInvariant:
    """force_flat applies only to bar-level economics; diagnostics unchanged."""

    def test_force_flat_does_not_change_diagnostics_length(self):
        """The OOS diagnostics length must equal effective_oos_bars + 1
        regardless of force-flat application (WP8 §force_flat invariant)."""
        from supertrend_optimizer.utils.time_utils import WFWindowSlice
        from wf_grid.wf.step_executor import execute_oos_step

        n, train_end = 120, 60

        @dataclass
        class _Status:
            min_meaningful_bars: int = 5

        @dataclass
        class _BT:
            commission: float = 0.001
            early_exit_max_drawdown: float = 0.5
            early_exit_check_bars: int = 0
            min_trades_required: int = 1

        @dataclass
        class _Val:
            warmup_period: int = 10
            min_oos_bars: int = 5
            min_train_bars: int = 5

        @dataclass
        class _Cfg:
            backtest: Any = field(default_factory=_BT)
            validation: Any = field(default_factory=_Val)
            status: Any = field(default_factory=_Status)
            resolved_periods_per_year: int = 252
            trade_filter: Any = field(default_factory=_FilterCfg)

        @dataclass
        class _GP:
            grid_point_id: str = "atr5_m2.0_revers"
            atr_period: int = 5
            multiplier: float = 2.0
            trade_mode: str = "revers"

        close = _make_prices(n)
        o, h, l, c = _ohlc(close)
        idx = pd.date_range("2020-01-01", periods=n, freq="D")
        wf_slice = WFWindowSlice(step_index=0,
                                  train_start_idx=0, train_end_idx=train_end,
                                  test_start_idx=train_end, test_end_idx=n)

        step = execute_oos_step(
            grid_point=_GP(), wf_slice=wf_slice,
            full_open=o, full_high=h, full_low=l, full_close=c,
            full_index=idx, config=_Cfg(),
            prepend_bars_requested=10,
            zigzag_global_stats=_make_global_stats(),
        )
        if step.filter_diagnostics_oos is not None:
            expected_len = step.effective_oos_bars + 1
            for key, arr in step.filter_diagnostics_oos.items():
                assert len(arr) == expected_len, (
                    f"diagnostics[{key!r}] len={len(arr)} != "
                    f"effective_oos_bars+1={expected_len}"
                )


# ===========================================================================
# R9. Defensive fallback: filter fields explicitly None
# ===========================================================================

class TestDefensiveFallbackFilterFields:
    """_defensive_fallback must produce filter_diagnostics_oos=None,
    filter_diagnostics_summary=None (WP11 explicit-None contract)."""

    def test_defensive_fallback_filter_fields_none(self):
        from wf_grid.wf.step_executor import _defensive_fallback
        from supertrend_optimizer.utils.time_utils import WFWindowSlice

        @dataclass
        class _FakeResult:
            metrics: dict = field(default_factory=lambda: {
                "sum_pnl_pct": 0.0, "num_trades": 0,
            })
            warmup: int = 10
            effective_warmup: int = 10
            early_exit: bool = False
            filter_diagnostics: Optional[Any] = None

        @dataclass
        class _GP:
            grid_point_id: str = "gp_test"

        wf_slice = WFWindowSlice(
            step_index=0,
            train_start_idx=0, train_end_idx=50,
            test_start_idx=50, test_end_idx=100,
        )
        step = _defensive_fallback(
            ext_result=_FakeResult(),
            grid_point=_GP(),
            wf_slice=wf_slice,
            prepend_bars_requested=10,
            prepend_bars_applied=10,
        )
        assert step.filter_diagnostics_oos is None, (
            "_defensive_fallback must set filter_diagnostics_oos=None"
        )
        assert step.filter_diagnostics_summary is None, (
            "_defensive_fallback must set filter_diagnostics_summary=None"
        )


# ===========================================================================
# R10. config.yaml production default has trade_filter disabled
# ===========================================================================

class TestProductionConfigDefault:
    """config.yaml must have trade_filter.enabled=false (safe rollout default)."""

    def test_config_yaml_trade_filter_disabled(self):
        """Plan §WP11 step 1: enabled=false stays default in production config."""
        try:
            import yaml  # type: ignore[import]
        except ImportError:
            pytest.skip("pyyaml not available")

        config_path = _PROJECT_ROOT / "config.yaml"
        if not config_path.exists():
            pytest.skip("config.yaml not found — skipping production config check")

        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        trade_filter = cfg.get("trade_filter")
        if trade_filter is None:
            # Absent trade_filter = disabled (spec §11.1)
            return
        assert trade_filter.get("enabled") is False, (
            "config.yaml trade_filter.enabled must be false (safe rollout default); "
            "enabling in production config without explicit experiment intent "
            "violates WP11 rollback contract."
        )
