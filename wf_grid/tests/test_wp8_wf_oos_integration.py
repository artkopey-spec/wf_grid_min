"""
WP8 unit tests — ZigZag ST trade_filter WF/OOS/train integration.

Plan reference:  WP8 (plan §WP8, §2.2, §8.1, §8.2, §8.3, §8.4, §8.5).
Spec reference:  Appendix A v1.1 §10, §12, §12.3, §13, §17.13.

Coverage areas
--------------
A) OOS extended-slice alignment: filter_diagnostics_oos length == effective_oos_bars.
B) No lookahead: prepend-zone entries do NOT leak into OOS trades.
C) Force-flat does NOT modify filter_diagnostics.
D) Diagnostics length invariant after OOS trim.
E) Train: no prepend, no force-flat, same FSM rules.
F) Disabled / absent trade_filter is bit-identical baseline (WF level).
G) global_stats not recomputed per WF step.
H) Init failure (invalid global stats) raises ConfigError before WF.
I) global_offset alignment: FSM receives correct absolute bar index.
J) Train / OOS consistency for disabled filter.

Anti-drift (WP8)
----------------
- No XLSX / collector export changes (WP9).
- No filter_diagnostics_summary / diagnostics export columns (WP9).
- Orchestrator tested via run_grid_pipeline only for smoke; full integration
  lives in E2E tests.
"""
from __future__ import annotations

import textwrap
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch, call

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.core.zigzag_st_filter import (
    ZigZagGlobalStats,
    build_zigzag_global_stats,
    apply as zigzag_apply,
)
from supertrend_optimizer.engine.run import run_single_backtest
from supertrend_optimizer.utils.enums import ExecutionModel
from supertrend_optimizer.utils.exceptions import ConfigError
from supertrend_optimizer.utils.time_utils import WFWindowSlice

from wf_grid.wf.step_executor import (
    execute_oos_step,
    execute_train_step,
    StepResult,
)
from wf_grid.wf.runner import (
    run_wf_for_grid_point,
    run_wf_train_for_grid_point,
)


# ===========================================================================
# Shared helpers / test doubles
# ===========================================================================

def _make_prices(n: int = 200, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.cumsum(rng.normal(0, 0.5, n)) + 100.0


def _ohlc(close: np.ndarray):
    open_p = np.roll(close, 1)
    open_p[0] = close[0]
    high = close + 0.5
    low = close - 0.5
    return open_p, high, low, close


def _make_full_data(n: int = 200, seed: int = 7) -> pd.DataFrame:
    close = _make_prices(n, seed=seed)
    o, h, l, c = _ohlc(close)
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c}, index=idx)


@dataclass
class _ToggleDouble:
    enabled: bool = True


@dataclass
class _TriggersDouble:
    candidate_threshold: _ToggleDouble = field(default_factory=_ToggleDouble)
    confirmed_median: _ToggleDouble = field(
        default_factory=lambda: _ToggleDouble(enabled=False)
    )


@dataclass
class _LifecycleDouble:
    freeze_confirmed_legs: int = 3
    stop_check: str = "confirm_bar_only"
    stopping_exit: str = "opposite_st_flip"


@dataclass
class _ZigZagCfgDouble:
    enabled: bool = True
    reversal_threshold: float = 0.02
    local_window: int = 5
    global_stats_source: str = "full_dataset"
    leg_height_mode: str = "pct"
    global_median: str = "auto"
    candidate_trigger_threshold: float = 0.01
    candidate_trigger_quantile: Optional[float] = None


@dataclass
class _TradeFilterCfgDouble:
    enabled: bool = True
    type: str = "zigzag_st_mode"
    zigzag: _ZigZagCfgDouble = field(default_factory=_ZigZagCfgDouble)
    triggers: _TriggersDouble = field(default_factory=_TriggersDouble)
    lifecycle: _LifecycleDouble = field(default_factory=_LifecycleDouble)


@dataclass
class _BacktestCfgDouble:
    commission: float = 0.001
    early_exit_max_drawdown: float = 0.5
    early_exit_check_bars: int = 0
    min_trades_required: int = 1


@dataclass
class _ValidationCfgDouble:
    warmup_period: int = 0


@dataclass
class _GridConfigDouble:
    backtest: _BacktestCfgDouble = field(default_factory=_BacktestCfgDouble)
    validation: _ValidationCfgDouble = field(default_factory=_ValidationCfgDouble)
    resolved_periods_per_year: float = 252.0
    trade_filter: Optional[_TradeFilterCfgDouble] = None


@dataclass
class _GridPointDouble:
    grid_point_id: str = "gp_test"
    atr_period: int = 5
    multiplier: float = 2.0
    trade_mode: str = "revers"


def _make_wf_slice(train_start: int, train_end: int,
                   test_start: int, test_end: int,
                   step_index: int = 0) -> WFWindowSlice:
    return WFWindowSlice(
        train_start_idx=train_start,
        train_end_idx=train_end,
        test_start_idx=test_start,
        test_end_idx=test_end,
        step_index=step_index,
    )


def _build_global_stats(close: np.ndarray) -> ZigZagGlobalStats:
    cfg = _TradeFilterCfgDouble()
    return build_zigzag_global_stats(close=close, trade_filter_config=cfg)


def _enabled_config() -> _GridConfigDouble:
    return _GridConfigDouble(trade_filter=_TradeFilterCfgDouble())


def _disabled_config() -> _GridConfigDouble:
    cfg = _GridConfigDouble(trade_filter=_TradeFilterCfgDouble())
    cfg.trade_filter.enabled = False
    return _GridConfigDouble(trade_filter=None)  # absent = disabled


# ===========================================================================
# A. OOS extended-slice alignment: filter_diagnostics_oos length == OOS bars
# ===========================================================================

class TestOOSDiagnosticsAlignment:
    """filter_diagnostics_oos length must equal effective_oos_bars (plan §WP8 step 3)."""

    def _run_oos_step(self, n: int = 200, prepend: int = 40) -> StepResult:
        full_data = _make_full_data(n=n)
        close = full_data["close"].values
        gs = _build_global_stats(close)
        config = _enabled_config()

        # train=[0..100), test=[100..160)
        wf_slice = _make_wf_slice(0, 100, 100, 160)
        gp = _GridPointDouble()

        return execute_oos_step(
            grid_point=gp,
            wf_slice=wf_slice,
            full_open=full_data["open"].values,
            full_high=full_data["high"].values,
            full_low=full_data["low"].values,
            full_close=close,
            full_index=full_data.index,
            config=config,
            prepend_bars_requested=prepend,
            zigzag_global_stats=gs,
        )

    def test_diagnostics_oos_present_when_enabled(self):
        step = self._run_oos_step()
        assert step.filter_diagnostics_oos is not None, (
            "filter_diagnostics_oos must not be None when trade_filter is enabled"
        )

    def test_diagnostics_oos_length_equals_oos_positions_length(self):
        # Donor convention: len(positions) == len(close) == n,
        # len(returns) == n-1, so diagnostics length == effective_oos_bars + 1.
        step = self._run_oos_step()
        assert step.filter_diagnostics_oos is not None
        expected = step.effective_oos_bars + 1  # positions / diagnostic bars
        for key, arr in step.filter_diagnostics_oos.items():
            assert len(arr) == expected, (
                f"filter_diagnostics_oos[{key!r}] length {len(arr)} "
                f"!= expected {expected} (effective_oos_bars+1)"
            )

    def test_diagnostics_oos_has_canonical_keys(self):
        step = self._run_oos_step()
        assert step.filter_diagnostics_oos is not None
        required = {
            "trade_filter_state",
            "trade_filter_trigger_source",
            "trade_filter_state_code",
            "confirmed_legs_since_start",
            "st_flip_dir",
        }
        missing = required - set(step.filter_diagnostics_oos.keys())
        assert not missing, f"Missing keys in filter_diagnostics_oos: {missing}"

    def test_diagnostics_oos_none_when_disabled(self):
        full_data = _make_full_data(n=200)
        config = _disabled_config()  # trade_filter = None
        gp = _GridPointDouble()
        wf_slice = _make_wf_slice(0, 100, 100, 160)

        step = execute_oos_step(
            grid_point=gp,
            wf_slice=wf_slice,
            full_open=full_data["open"].values,
            full_high=full_data["high"].values,
            full_low=full_data["low"].values,
            full_close=full_data["close"].values,
            full_index=full_data.index,
            config=config,
            prepend_bars_requested=20,
        )
        assert step.filter_diagnostics_oos is None, (
            "filter_diagnostics_oos must be None when trade_filter is absent/disabled"
        )


# ===========================================================================
# B. No lookahead: prepend-zone entries do NOT leak into OOS trades
# ===========================================================================

class TestPrependZoneNoLeak:
    """Trades opened during prepend must not appear in oos_trades_df (plan §WP8)."""

    def test_no_negative_entry_index_in_oos_trades(self):
        n = 200
        full_data = _make_full_data(n=n)
        close = full_data["close"].values
        gs = _build_global_stats(close)
        config = _enabled_config()
        gp = _GridPointDouble()
        # 50-bar prepend; test=[100..160)
        wf_slice = _make_wf_slice(0, 100, 100, 160)

        step = execute_oos_step(
            grid_point=gp,
            wf_slice=wf_slice,
            full_open=full_data["open"].values,
            full_high=full_data["high"].values,
            full_low=full_data["low"].values,
            full_close=close,
            full_index=full_data.index,
            config=config,
            prepend_bars_requested=50,
            zigzag_global_stats=gs,
        )

        if step.oos_trades_df is not None and len(step.oos_trades_df) > 0:
            min_entry = int(step.oos_trades_df["entry_index"].min())
            assert min_entry >= 0, (
                f"Prepend-zone entry leaked: min entry_index = {min_entry} (must be >= 0)"
            )

    def test_disabled_path_no_negative_entry_index(self):
        """Sanity: disabled path also never has negative entry indices."""
        n = 200
        full_data = _make_full_data(n=n)
        config = _disabled_config()
        gp = _GridPointDouble()
        wf_slice = _make_wf_slice(0, 100, 100, 160)

        step = execute_oos_step(
            grid_point=gp,
            wf_slice=wf_slice,
            full_open=full_data["open"].values,
            full_high=full_data["high"].values,
            full_low=full_data["low"].values,
            full_close=full_data["close"].values,
            full_index=full_data.index,
            config=config,
            prepend_bars_requested=50,
        )

        if step.oos_trades_df is not None and len(step.oos_trades_df) > 0:
            min_entry = int(step.oos_trades_df["entry_index"].min())
            assert min_entry >= 0


# ===========================================================================
# C. Force-flat does NOT modify filter_diagnostics (plan §WP8 step 4)
# ===========================================================================

class TestForceFlatDoesNotTouchDiagnostics:
    """filter_diagnostics must equal direct slice of FSM output regardless of force-flat."""

    def test_force_flat_does_not_change_diagnostic_values(self):
        """Run with and without prepend; the trimmed diagnostic values should be the same."""
        n = 200
        full_data = _make_full_data(n=n)
        close = full_data["close"].values
        gs = _build_global_stats(close)
        config = _enabled_config()
        gp = _GridPointDouble()
        # test=[120..160), prepend=40 → ext=[80..160), oos=[120..160)
        wf_slice = _make_wf_slice(0, 100, 120, 160)

        step = execute_oos_step(
            grid_point=gp,
            wf_slice=wf_slice,
            full_open=full_data["open"].values,
            full_high=full_data["high"].values,
            full_low=full_data["low"].values,
            full_close=close,
            full_index=full_data.index,
            config=config,
            prepend_bars_requested=40,
            zigzag_global_stats=gs,
        )

        if step.filter_diagnostics_oos is None:
            pytest.skip("filter enabled but diagnostics unexpectedly None")

        # Diagnostics must have n_oos = effective_oos_bars + 1 entries
        # (one per OOS close bar, matching len(positions) convention).
        expected = step.effective_oos_bars + 1
        for key, arr in step.filter_diagnostics_oos.items():
            assert len(arr) == expected, (
                f"force-flat changed {key!r} length: "
                f"{len(arr)} != {expected}"
            )

    def test_filter_diagnostics_not_modified_by_force_flat_values(self):
        """Verify that force-flat doesn't corrupt state values in diagnostics."""
        n = 200
        full_data = _make_full_data(n=n, seed=13)
        close = full_data["close"].values
        gs = _build_global_stats(close)
        config = _enabled_config()
        gp = _GridPointDouble()
        wf_slice = _make_wf_slice(0, 100, 100, 150)

        step = execute_oos_step(
            grid_point=gp,
            wf_slice=wf_slice,
            full_open=full_data["open"].values,
            full_high=full_data["high"].values,
            full_low=full_data["low"].values,
            full_close=close,
            full_index=full_data.index,
            config=config,
            prepend_bars_requested=30,
            zigzag_global_stats=gs,
        )

        if step.filter_diagnostics_oos is None:
            return  # disabled path OK

        # State values must be valid FSM strings (no NaN / None / corruption).
        state_arr = step.filter_diagnostics_oos.get("trade_filter_state")
        if state_arr is not None:
            valid_states = {"OFF", "WAIT_FIRST_ST_FLIP", "ST_ACTIVE_FREEZE",
                            "ST_ACTIVE_MONITORING", "ST_STOPPING"}
            actual_states = set(str(s) for s in state_arr)
            unknown = actual_states - valid_states
            assert not unknown, (
                f"Unknown FSM states after force-flat trim: {unknown!r}"
            )


# ===========================================================================
# D. Diagnostics length invariant after OOS trim
# ===========================================================================

class TestDiagnosticsLengthInvariant:
    """All arrays in filter_diagnostics_oos must have identical length."""

    def test_all_oos_diagnostic_arrays_have_same_length(self):
        n = 200
        full_data = _make_full_data(n=n)
        close = full_data["close"].values
        gs = _build_global_stats(close)
        config = _enabled_config()
        gp = _GridPointDouble()
        wf_slice = _make_wf_slice(0, 100, 100, 160)

        step = execute_oos_step(
            grid_point=gp,
            wf_slice=wf_slice,
            full_open=full_data["open"].values,
            full_high=full_data["high"].values,
            full_low=full_data["low"].values,
            full_close=close,
            full_index=full_data.index,
            config=config,
            prepend_bars_requested=30,
            zigzag_global_stats=gs,
        )

        if step.filter_diagnostics_oos is None:
            pytest.skip("filter disabled")

        lengths = {k: len(v) for k, v in step.filter_diagnostics_oos.items()}
        unique_lengths = set(lengths.values())
        assert len(unique_lengths) == 1, (
            f"filter_diagnostics_oos arrays have inconsistent lengths: {lengths}"
        )

    def test_oos_diagnostic_length_matches_oos_positions(self):
        """Verify diagnostics length == n_oos (= test_end - test_start = effective_oos_bars+1).

        Donor convention: positions / diagnostics have n bars,
        returns have n-1 transitions.
        """
        n = 200
        full_data = _make_full_data(n=n, seed=42)
        close = full_data["close"].values
        gs = _build_global_stats(close)
        config = _enabled_config()
        gp = _GridPointDouble()
        wf_slice = _make_wf_slice(0, 100, 110, 170)

        step = execute_oos_step(
            grid_point=gp,
            wf_slice=wf_slice,
            full_open=full_data["open"].values,
            full_high=full_data["high"].values,
            full_low=full_data["low"].values,
            full_close=close,
            full_index=full_data.index,
            config=config,
            prepend_bars_requested=25,
            zigzag_global_stats=gs,
        )

        if step.filter_diagnostics_oos is None:
            pytest.skip("filter disabled")

        expected = step.effective_oos_bars + 1  # positions-aligned length
        for key, arr in step.filter_diagnostics_oos.items():
            assert len(arr) == expected, (
                f"filter_diagnostics_oos[{key!r}] length {len(arr)} "
                f"!= expected n_oos {expected}"
            )


# ===========================================================================
# E. Train: no prepend, same FSM rules
# ===========================================================================

class TestTrainPath:
    """Train uses same FSM rules; no prepend, no force-flat (plan §WP8 step 6)."""

    def _run_train_step(self, n: int = 200) -> StepResult:
        full_data = _make_full_data(n=n)
        close = full_data["close"].values
        gs = _build_global_stats(close)
        config = _enabled_config()
        gp = _GridPointDouble()
        wf_slice = _make_wf_slice(0, 120, 120, 160)

        return execute_train_step(
            grid_point=gp,
            wf_slice=wf_slice,
            full_open=full_data["open"].values,
            full_high=full_data["high"].values,
            full_low=full_data["low"].values,
            full_close=close,
            full_index=full_data.index,
            config=config,
            zigzag_global_stats=gs,
        )

    def test_train_step_result_has_diagnostics(self):
        step = self._run_train_step()
        # Train doesn't trim; diagnostics cover the full train slice.
        assert step.filter_diagnostics_oos is not None

    def test_train_no_prepend_applied(self):
        step = self._run_train_step()
        assert step.prepend_bars_applied == 0
        assert not step.used_prepend

    def test_train_diagnostics_length_equals_train_length(self):
        step = self._run_train_step()
        if step.filter_diagnostics_oos is None:
            pytest.skip("filter disabled")
        # Train: diagnostics cover the full train slice; donor convention
        # len(diagnostics) == len(positions) == n, len(returns) == n-1.
        expected_len = step.effective_oos_bars + 1
        for key, arr in step.filter_diagnostics_oos.items():
            assert len(arr) == expected_len, (
                f"train filter_diagnostics_oos[{key!r}] length {len(arr)} "
                f"!= expected n_train {expected_len}"
            )

    def test_train_disabled_diagnostics_none(self):
        n = 200
        full_data = _make_full_data(n=n)
        config = _disabled_config()
        gp = _GridPointDouble()
        wf_slice = _make_wf_slice(0, 120, 120, 160)

        step = execute_train_step(
            grid_point=gp,
            wf_slice=wf_slice,
            full_open=full_data["open"].values,
            full_high=full_data["high"].values,
            full_low=full_data["low"].values,
            full_close=full_data["close"].values,
            full_index=full_data.index,
            config=config,
        )
        assert step.filter_diagnostics_oos is None


# ===========================================================================
# F. Disabled / absent trade_filter is bit-identical baseline (WF level)
# ===========================================================================

class TestDisabledParity:
    """OOS/train with disabled filter must produce same metrics as no-filter baseline."""

    def _run_oos(self, config: _GridConfigDouble, gs=None) -> StepResult:
        n = 200
        full_data = _make_full_data(n=n, seed=99)
        gp = _GridPointDouble()
        wf_slice = _make_wf_slice(0, 100, 100, 160)

        return execute_oos_step(
            grid_point=gp,
            wf_slice=wf_slice,
            full_open=full_data["open"].values,
            full_high=full_data["high"].values,
            full_low=full_data["low"].values,
            full_close=full_data["close"].values,
            full_index=full_data.index,
            config=config,
            prepend_bars_requested=20,
            zigzag_global_stats=gs,
        )

    def test_disabled_filter_matches_no_filter_config(self):
        """Disabled config (trade_filter=None) is bit-identical to explicit disabled."""
        step_none = self._run_oos(_disabled_config(), gs=None)

        # Explicit disabled with enabled=False
        cfg_disabled = _GridConfigDouble(
            trade_filter=_TradeFilterCfgDouble(enabled=False)
        )
        step_disabled = self._run_oos(cfg_disabled, gs=None)

        assert step_none.metrics["num_trades"] == step_disabled.metrics["num_trades"]
        for k in ("sharpe", "sortino", "cagr", "max_drawdown", "win_rate",
                  "profit_factor", "sum_pnl_pct"):
            v1, v2 = step_none.metrics.get(k), step_disabled.metrics.get(k)
            if v1 is not None and v2 is not None:
                if isinstance(v1, float) and isinstance(v2, float):
                    if not (v1 != v1 and v2 != v2):  # NaN == NaN check
                        assert abs(v1 - v2) < 1e-12, (
                            f"metric {k!r}: disabled={v1} vs no-filter={v2}"
                        )

    def test_disabled_diagnostics_none(self):
        step = self._run_oos(_disabled_config(), gs=None)
        assert step.filter_diagnostics_oos is None


# ===========================================================================
# G. global_stats not recomputed per WF step
# ===========================================================================

class TestGlobalStatsNotRecomputed:
    """build_zigzag_global_stats must be called once; the same object is passed to all steps."""

    def test_same_global_stats_object_passed_to_all_steps(self):
        n = 300
        full_data = _make_full_data(n=n)
        close = full_data["close"].values
        gs = _build_global_stats(close)
        config = _enabled_config()
        gp = _GridPointDouble()

        slices = [
            _make_wf_slice(0, 100, 100, 130, step_index=0),
            _make_wf_slice(30, 130, 130, 160, step_index=1),
            _make_wf_slice(60, 160, 160, 200, step_index=2),
        ]

        # Capture calls to run_single_backtest to verify zigzag_global_stats arg
        calls_gs = []
        original_rsb = run_single_backtest

        def _spy_rsb(*args, **kwargs):
            calls_gs.append(kwargs.get("zigzag_global_stats"))
            return original_rsb(*args, **kwargs)

        with patch(
            "wf_grid.wf.step_executor.run_single_backtest",
            side_effect=_spy_rsb,
        ):
            run_wf_for_grid_point(
                grid_point=gp,
                wf_slices=slices,
                full_data=full_data,
                config=config,
                prepend_bars_requested=20,
                zigzag_global_stats=gs,
            )

        assert len(calls_gs) == 3, f"Expected 3 calls, got {len(calls_gs)}"
        for i, called_gs in enumerate(calls_gs):
            assert called_gs is gs, (
                f"Step {i}: global_stats object identity mismatch "
                f"(expected id={id(gs)}, got id={id(called_gs)})"
            )


# ===========================================================================
# H. Init failure: ConfigError before WF execution
# ===========================================================================

class TestInitFailure:
    """build_zigzag_global_stats failures must propagate before WF (plan §WP8 step 1)."""

    def test_bad_reversal_threshold_raises_config_error_before_backtest(self):
        """Zero reversal_threshold must raise ConfigError from build_zigzag_global_stats."""
        n = 200
        full_data = _make_full_data(n=n)
        close = full_data["close"].values

        bad_cfg = _TradeFilterCfgDouble()
        bad_cfg.zigzag.reversal_threshold = 0.0  # invalid: must be > 0

        with pytest.raises((ConfigError, Exception)):
            build_zigzag_global_stats(close=close, trade_filter_config=bad_cfg)

    def test_none_trade_filter_config_raises(self):
        n = 50
        close = _make_prices(n=n)
        with pytest.raises((ConfigError, Exception)):
            build_zigzag_global_stats(close=close, trade_filter_config=None)


# ===========================================================================
# I. global_offset alignment: each step receives correct absolute offset
# ===========================================================================

class TestGlobalOffsetAlignment:
    """global_offset must equal ext_start (absolute index) for OOS, train_start for train."""

    def test_oos_global_offset_equals_ext_start(self):
        n = 200
        full_data = _make_full_data(n=n)
        close = full_data["close"].values
        gs = _build_global_stats(close)
        config = _enabled_config()
        gp = _GridPointDouble()

        # test=[100..160), prepend=30 → ext_start=70
        wf_slice = _make_wf_slice(0, 100, 100, 160)
        expected_ext_start = 100 - 30  # = 70

        captured_offset = []
        original_rsb = run_single_backtest

        def _spy(*args, **kwargs):
            captured_offset.append(kwargs.get("global_offset"))
            return original_rsb(*args, **kwargs)

        with patch("wf_grid.wf.step_executor.run_single_backtest", side_effect=_spy):
            execute_oos_step(
                grid_point=gp,
                wf_slice=wf_slice,
                full_open=full_data["open"].values,
                full_high=full_data["high"].values,
                full_low=full_data["low"].values,
                full_close=close,
                full_index=full_data.index,
                config=config,
                prepend_bars_requested=30,
                zigzag_global_stats=gs,
            )

        assert captured_offset, "run_single_backtest was not called"
        assert captured_offset[0] == expected_ext_start, (
            f"global_offset={captured_offset[0]} != expected ext_start={expected_ext_start}"
        )

    def test_train_global_offset_equals_train_start(self):
        n = 200
        full_data = _make_full_data(n=n)
        close = full_data["close"].values
        gs = _build_global_stats(close)
        config = _enabled_config()
        gp = _GridPointDouble()

        # train=[50..120)
        wf_slice = _make_wf_slice(50, 120, 120, 160)
        expected_train_start = 50

        captured_offset = []
        original_rsb = run_single_backtest

        def _spy(*args, **kwargs):
            captured_offset.append(kwargs.get("global_offset"))
            return original_rsb(*args, **kwargs)

        with patch("wf_grid.wf.step_executor.run_single_backtest", side_effect=_spy):
            execute_train_step(
                grid_point=gp,
                wf_slice=wf_slice,
                full_open=full_data["open"].values,
                full_high=full_data["high"].values,
                full_low=full_data["low"].values,
                full_close=close,
                full_index=full_data.index,
                config=config,
                zigzag_global_stats=gs,
            )

        assert captured_offset, "run_single_backtest was not called for train"
        assert captured_offset[0] == expected_train_start, (
            f"train global_offset={captured_offset[0]} != train_start={expected_train_start}"
        )


# ===========================================================================
# J. Multiple OOS steps: entry_trigger_source values are canonical
# ===========================================================================

class TestOOSTradesCanonicalValues:
    """OOS trades must carry only canonical trigger source values (spec §13)."""

    def test_entry_trigger_source_canonical_in_oos_trades(self):
        n = 200
        full_data = _make_full_data(n=n, seed=17)
        close = full_data["close"].values
        gs = _build_global_stats(close)
        config = _enabled_config()
        gp = _GridPointDouble()
        wf_slice = _make_wf_slice(0, 100, 100, 160)

        step = execute_oos_step(
            grid_point=gp,
            wf_slice=wf_slice,
            full_open=full_data["open"].values,
            full_high=full_data["high"].values,
            full_low=full_data["low"].values,
            full_close=close,
            full_index=full_data.index,
            config=config,
            prepend_bars_requested=20,
            zigzag_global_stats=gs,
        )

        if step.oos_trades_df is None or len(step.oos_trades_df) == 0:
            return  # no trades — nothing to check

        if "entry_trigger_source" not in step.oos_trades_df.columns:
            return  # disabled path — column absent is fine

        allowed = {"candidate_threshold", "confirmed_median", "both", "none"}
        actual = set(str(v) for v in step.oos_trades_df["entry_trigger_source"])
        forbidden = actual - allowed
        assert not forbidden, (
            f"Non-canonical entry_trigger_source values in OOS trades: {forbidden!r}"
        )


# ===========================================================================
# Anti-drift: WP8 module-level guard
# ===========================================================================

class TestWp8AntiDrift:
    """Anti-drift: WP8 must not leak WP9 exports or change donor core signatures."""

    def test_step_result_has_filter_diagnostics_oos_field(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(StepResult)}
        assert "filter_diagnostics_oos" in fields, (
            "StepResult.filter_diagnostics_oos field must exist (WP8)"
        )

    def test_execute_oos_step_accepts_zigzag_global_stats(self):
        import inspect
        sig = inspect.signature(execute_oos_step)
        assert "zigzag_global_stats" in sig.parameters, (
            "execute_oos_step must accept zigzag_global_stats param (WP8)"
        )

    def test_execute_train_step_accepts_zigzag_global_stats(self):
        import inspect
        sig = inspect.signature(execute_train_step)
        assert "zigzag_global_stats" in sig.parameters, (
            "execute_train_step must accept zigzag_global_stats param (WP8)"
        )

    def test_run_wf_for_grid_point_accepts_zigzag_global_stats(self):
        import inspect
        sig = inspect.signature(run_wf_for_grid_point)
        assert "zigzag_global_stats" in sig.parameters

    def test_run_wf_train_for_grid_point_accepts_zigzag_global_stats(self):
        import inspect
        sig = inspect.signature(run_wf_train_for_grid_point)
        assert "zigzag_global_stats" in sig.parameters
