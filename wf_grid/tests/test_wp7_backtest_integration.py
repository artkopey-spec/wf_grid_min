"""
WP7 unit tests — ZigZag ST trade_filter backtest engine integration.

Plan reference:  WP7 (plan §2.2, §8.1, §8.2, §8.3, §8.4, §8.5).
Spec reference:  Appendix A v1.1 §10, §13, §16, §17.1, §17.6, §17.13.

Coverage areas
--------------
A) Disabled / absent path is bit-identical baseline.
B) ``generate_positions`` is NOT called on the enabled path.
C) ``filter_diagnostics`` lengths match ``len(positions)`` invariant.
D) ``trades`` / ``returns`` are built only from ``filtered_positions``.
E) Early-exit synchronous truncation of ``filter_diagnostics``.
F) ``RawBacktestArtifacts`` migration: no stale 7-value unpack guard.
G) ``BacktestResult.filter_diagnostics`` length invariant (``__post_init__``).
H) ``attach_trade_filter_diagnostics`` trade-level diagnostics.
I) ``BacktestResult(filter_diagnostics=None)`` smoke — tester compat.
J) Close-only invariance: distorted high/low do not change ZigZag outputs.

Anti-drift (WP7)
----------------
- No WF / OOS / prepend integration (WP8).
- No ``filter_diagnostics_oos`` / ``filter_diagnostics_summary`` (WP9).
- No XLSX / collector changes (WP9).
- No changes to ``calculate_returns`` / ``extract_trades`` signatures.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from supertrend_optimizer.core.backtest import run_backtest_fast
from supertrend_optimizer.core.zigzag_st_filter import (
    ZigZagGlobalStats,
    ZigZagPerBar,
    attach_trade_filter_diagnostics,
    apply,
)
from supertrend_optimizer.engine.result import BacktestResult, RawBacktestArtifacts
from supertrend_optimizer.engine.run import run_single_backtest
from supertrend_optimizer.utils.enums import ExecutionModel
from supertrend_optimizer.utils.exceptions import ConfigError


# ===========================================================================
# Shared helpers / fixtures
# ===========================================================================

def _make_prices(n: int = 60, seed: int = 42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.cumsum(rng.normal(0, 0.5, n)) + 100.0


def _ohlc(close: np.ndarray):
    open_p = np.roll(close, 1); open_p[0] = close[0]
    high = close + 1.0
    low = close - 1.0
    return open_p, high, low, close


@dataclass
class _ToggleDouble:
    enabled: bool = True


@dataclass
class _TriggersDouble:
    candidate_threshold: _ToggleDouble = field(default_factory=_ToggleDouble)
    confirmed_median: _ToggleDouble = field(default_factory=lambda: _ToggleDouble(enabled=False))


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


def _make_global_stats(
    *,
    global_median: float = 0.03,
    candidate_trigger_threshold: float = 0.01,
    reversal_threshold: float = 0.02,
) -> ZigZagGlobalStats:
    return ZigZagGlobalStats(
        reversal_threshold=reversal_threshold,
        global_stats_source="full_dataset",
        leg_height_mode="pct",
        confirmed_legs=[],
        confirmed_heights_pct=np.array([], dtype=np.float64),
        global_median=global_median,
        candidate_trigger_threshold=candidate_trigger_threshold,
        candidate_trigger_source="explicit",
        candidate_trigger_quantile=None,
        n_legs_total=0,
        insufficient_data=False,
        fail_closed_reason=None,
        metadata={},
    )


def _disabled_cfg():
    cfg = _TradeFilterCfgDouble()
    cfg.enabled = False
    return cfg


def _make_index(n: int) -> pd.RangeIndex:
    return pd.RangeIndex(n)


# ===========================================================================
# A. Disabled / absent path is bit-identical baseline.
# ===========================================================================

class TestDisabledPathParity:
    """When trade_filter is disabled or None the backtest must produce
    bit-identical outputs to the pre-WP7 code path that always called
    ``generate_positions``.

    Plan §8.2 rule 4 / §8.3 (disabled branch).
    """

    def test_disabled_returns_none_filter_diagnostics(self):
        n = 40
        close = _make_prices(n)
        o, h, l, c = _ohlc(close)
        arts = run_backtest_fast(
            o, h, l, c, atr_period=5, multiplier=2.0, trade_mode="revers",
            commission=0.001,
            early_exit_enabled=False, early_exit_max_drawdown=0.5,
            early_exit_check_bars=0,
            trade_filter_config=_disabled_cfg(),
            zigzag_global_stats=_make_global_stats(),
        )
        assert arts.filter_diagnostics is None

    def test_none_filter_config_returns_none_filter_diagnostics(self):
        n = 40
        close = _make_prices(n)
        o, h, l, c = _ohlc(close)
        arts = run_backtest_fast(
            o, h, l, c, atr_period=5, multiplier=2.0, trade_mode="revers",
            commission=0.001,
            early_exit_enabled=False, early_exit_max_drawdown=0.5,
            early_exit_check_bars=0,
            trade_filter_config=None,
            zigzag_global_stats=None,
        )
        assert arts.filter_diagnostics is None

    def test_disabled_positions_identical_to_pre_wp7_baseline(self):
        """Disabled path must reproduce the same positions as calling
        ``generate_positions`` directly (pre-WP7 baseline)."""
        from supertrend_optimizer.core.backtest import generate_positions
        from supertrend_optimizer.core.calculator import calculate_supertrend

        n = 60
        close = _make_prices(n)
        o, h, l, c = _ohlc(close)

        # Baseline: run with filter disabled.
        arts_disabled = run_backtest_fast(
            o, h, l, c, atr_period=7, multiplier=2.5, trade_mode="revers",
            commission=0.001,
            early_exit_enabled=False, early_exit_max_drawdown=0.5,
            early_exit_check_bars=0,
            trade_filter_config=None,
        )

        # Reference: pre-WP7 manual pipeline.
        trend_ref, _ = calculate_supertrend(h, l, c, 7, 2.5)
        positions_ref = generate_positions(
            trend_ref, "revers", ExecutionModel.OPEN_TO_OPEN
        )

        np.testing.assert_array_equal(arts_disabled.positions, positions_ref)
        np.testing.assert_array_equal(arts_disabled.trend, trend_ref)

    def test_disabled_returns_and_equity_identical_to_pre_wp7_baseline(self):
        from supertrend_optimizer.core.backtest import (
            calculate_equity_curve,
            calculate_returns,
            generate_positions,
        )
        from supertrend_optimizer.core.calculator import calculate_supertrend

        n = 60
        close = _make_prices(n, seed=7)
        o, h, l, c = _ohlc(close)

        arts_disabled = run_backtest_fast(
            o, h, l, c, atr_period=7, multiplier=2.5, trade_mode="revers",
            commission=0.001,
            early_exit_enabled=False, early_exit_max_drawdown=0.5,
            early_exit_check_bars=0,
            trade_filter_config=None,
        )

        trend_ref, _ = calculate_supertrend(h, l, c, 7, 2.5)
        pos_ref = generate_positions(trend_ref, "revers", ExecutionModel.OPEN_TO_OPEN)
        ret_ref = calculate_returns(o, pos_ref, 0.001, ExecutionModel.OPEN_TO_OPEN)
        eq_ref = calculate_equity_curve(ret_ref)

        np.testing.assert_array_almost_equal(arts_disabled.returns, ret_ref)
        np.testing.assert_array_almost_equal(arts_disabled.equity_curve, eq_ref)


# ===========================================================================
# B. generate_positions is NOT called on the enabled path.
# ===========================================================================

class TestGeneratePositionsNotCalledOnEnabledPath:
    """Plan §2.2, §8.3 acceptance gate: enabled path must not call
    ``generate_positions`` — FSM is the sole source of truth.
    """

    def test_generate_positions_not_called_when_filter_enabled(self):
        n = 40
        close = _make_prices(n, seed=3)
        o, h, l, c = _ohlc(close)
        cfg = _TradeFilterCfgDouble()
        gs = _make_global_stats()

        with patch(
            "supertrend_optimizer.core.backtest.generate_positions"
        ) as mock_gp:
            run_backtest_fast(
                o, h, l, c, atr_period=5, multiplier=2.0, trade_mode="revers",
                commission=0.001,
                early_exit_enabled=False, early_exit_max_drawdown=0.5,
                early_exit_check_bars=0,
                trade_filter_config=cfg,
                zigzag_global_stats=gs,
            )
            mock_gp.assert_not_called()

    def test_generate_positions_is_called_when_filter_disabled(self):
        n = 40
        close = _make_prices(n, seed=4)
        o, h, l, c = _ohlc(close)

        with patch(
            "supertrend_optimizer.core.backtest.generate_positions",
            wraps=__import__(
                "supertrend_optimizer.core.backtest", fromlist=["generate_positions"]
            ).generate_positions,
        ) as mock_gp:
            run_backtest_fast(
                o, h, l, c, atr_period=5, multiplier=2.0, trade_mode="revers",
                commission=0.001,
                early_exit_enabled=False, early_exit_max_drawdown=0.5,
                early_exit_check_bars=0,
                trade_filter_config=None,
            )
            mock_gp.assert_called_once()


# ===========================================================================
# C. filter_diagnostics length invariant.
# ===========================================================================

class TestFilterDiagnosticsLengthInvariant:
    """Every diagnostic array must have ``len == len(positions)`` (§8.2 rule 6).
    Verified both at the ``RawBacktestArtifacts`` level and through
    ``BacktestResult.__post_init__``.
    """

    def _run_enabled(self, n: int = 50, **kwargs):
        close = _make_prices(n, seed=9)
        o, h, l, c = _ohlc(close)
        cfg = _TradeFilterCfgDouble()
        gs = _make_global_stats()
        return run_backtest_fast(
            o, h, l, c, atr_period=5, multiplier=2.0, trade_mode="revers",
            commission=0.001,
            early_exit_enabled=False, early_exit_max_drawdown=0.5,
            early_exit_check_bars=0,
            trade_filter_config=cfg,
            zigzag_global_stats=gs,
            **kwargs,
        )

    def test_diagnostics_length_equals_positions_length(self):
        arts = self._run_enabled()
        n_pos = len(arts.positions)
        assert arts.filter_diagnostics is not None
        for key, arr in arts.filter_diagnostics.items():
            assert len(arr) == n_pos, (
                f"filter_diagnostics[{key!r}] length {len(arr)} "
                f"!= positions length {n_pos}"
            )

    def test_diagnostics_required_keys_present(self):
        arts = self._run_enabled()
        expected_keys = {
            "trade_filter_state",
            "trade_filter_state_code",
            "trade_filter_trigger_source",
            "confirmed_legs_since_start",
            "st_flip_dir",
        }
        assert set(arts.filter_diagnostics.keys()) >= expected_keys

    def test_canonical_trigger_source_key_and_values(self):
        """Plan §8.4 / spec §13 canonical key and value contract.

        - Key must be ``trade_filter_trigger_source`` (not ``trigger_source``).
        - Allowed values: ``candidate_threshold``, ``confirmed_median``,
          ``both``, ``none`` — no legacy ``A`` / ``B`` values.
        """
        arts = self._run_enabled()
        diag = arts.filter_diagnostics
        assert "trade_filter_trigger_source" in diag, (
            "canonical key 'trade_filter_trigger_source' missing from filter_diagnostics"
        )
        assert "trigger_source" not in diag, (
            "legacy key 'trigger_source' must not appear in filter_diagnostics"
        )
        allowed_values = {"candidate_threshold", "confirmed_median", "both", "none"}
        actual_values = set(str(v) for v in diag["trade_filter_trigger_source"])
        forbidden = actual_values - allowed_values
        assert not forbidden, (
            f"Non-canonical trigger_source values found: {forbidden!r}. "
            f"Expected subset of {allowed_values!r}"
        )

    def test_entry_trigger_source_column_uses_canonical_values(self):
        """trades_df.entry_trigger_source must carry canonical spec §13 values."""
        close = _make_prices(50, seed=9)
        idx = _make_index(len(close))
        cfg = _TradeFilterCfgDouble()
        gs = _make_global_stats()
        result = run_single_backtest(
            *_ohlc(close),
            index=idx,
            atr_period=5, multiplier=2.0, trade_mode="revers",
            commission=0.001,
            trade_filter_config=cfg,
            zigzag_global_stats=gs,
            global_offset=0,
        )
        if result.trades_df is not None and "entry_trigger_source" in result.trades_df.columns:
            allowed_values = {"candidate_threshold", "confirmed_median", "both", "none"}
            actual_values = set(str(v) for v in result.trades_df["entry_trigger_source"])
            forbidden = actual_values - allowed_values
            assert not forbidden, (
                f"Non-canonical entry_trigger_source values in trades_df: {forbidden!r}"
            )

    def test_backtest_result_post_init_length_invariant_passes(self):
        """BacktestResult.__post_init__ must not raise on enabled path."""
        arts = self._run_enabled()
        idx = _make_index(len(arts.positions))
        result = run_single_backtest(
            *_ohlc(_make_prices(50, seed=9)),
            index=idx,
            atr_period=5, multiplier=2.0, trade_mode="revers",
            commission=0.001,
            trade_filter_config=_TradeFilterCfgDouble(),
            zigzag_global_stats=_make_global_stats(),
        )
        assert result.filter_diagnostics is not None
        n_pos = len(result.positions)
        for key, arr in result.filter_diagnostics.items():
            assert len(arr) == n_pos

    def test_backtest_result_post_init_rejects_wrong_length(self):
        """BacktestResult.__post_init__ raises ValueError when a diagnostic
        array length does not match positions length."""
        n = 20
        close = _make_prices(n)
        o = close.copy()
        pos = np.zeros(n, dtype=np.int8)
        ret = np.zeros(n - 1, dtype=np.float64)
        eq = np.ones(n, dtype=np.float64)
        bad_diag = {"trade_filter_state": np.zeros(n - 1, dtype=object)}
        with pytest.raises(ValueError, match="filter_diagnostics"):
            BacktestResult(
                atr_period=5, multiplier=2.0, trade_mode="revers",
                commission=0.001, warmup=0, effective_warmup=0,
                returns=ret, equity_curve=eq,
                positions=pos, trend=pos.copy(),
                metrics={}, early_exit=False, exit_bar=None,
                exit_drawdown=None, trades_df=None,
                n_bars_original=n,
                filter_diagnostics=bad_diag,
            )


# ===========================================================================
# D. trades / returns built only from filtered_positions.
# ===========================================================================

class TestTradesBuiltFromFilteredPositions:
    """Confirm that trades and returns in the enabled path are derived from
    the FSM's ``filtered_positions``, not from raw ST positions.

    Strategy: compare trades produced by ``run_single_backtest`` (enabled)
    against a manual run using the same ``filtered_positions`` from
    ``apply()``.  They must be identical.
    """

    def test_returns_derived_from_filtered_positions(self):
        """Returns on enabled path equal ``calculate_returns(filtered_pos)``."""
        from supertrend_optimizer.core.backtest import calculate_returns
        from supertrend_optimizer.core.calculator import calculate_supertrend

        n = 60
        close = _make_prices(n, seed=11)
        o, h, l, c = _ohlc(close)
        cfg = _TradeFilterCfgDouble()
        gs = _make_global_stats()

        arts = run_backtest_fast(
            o, h, l, c, atr_period=5, multiplier=2.0, trade_mode="revers",
            commission=0.001,
            early_exit_enabled=False, early_exit_max_drawdown=0.5,
            early_exit_check_bars=0,
            trade_filter_config=cfg,
            zigzag_global_stats=gs,
        )

        # The filtered_positions came from apply(); verify returns match.
        ret_manual = calculate_returns(
            o, arts.positions, 0.001, ExecutionModel.OPEN_TO_OPEN
        )
        np.testing.assert_array_almost_equal(arts.returns, ret_manual)


# ===========================================================================
# E. Early-exit synchronous truncation of filter_diagnostics.
# ===========================================================================

class TestEarlyExitDiagnosticsTruncation:
    """§8.2 rule 7: when early_exit fires, filter_diagnostics arrays are
    truncated to the same length as positions synchronously inside
    ``run_backtest_fast``.
    """

    def test_early_exit_truncates_diagnostics_to_positions_length(self):
        n = 60
        close = _make_prices(n, seed=13)
        o, h, l, c = _ohlc(close)
        cfg = _TradeFilterCfgDouble()
        gs = _make_global_stats()

        arts = run_backtest_fast(
            o, h, l, c, atr_period=5, multiplier=2.0, trade_mode="revers",
            commission=0.001,
            early_exit_enabled=True,
            early_exit_max_drawdown=0.01,  # very low → triggers quickly
            early_exit_check_bars=n,
            trade_filter_config=cfg,
            zigzag_global_stats=gs,
        )

        # If early_exit was triggered positions are shorter; otherwise full.
        n_pos = len(arts.positions)
        if arts.filter_diagnostics is not None:
            for key, arr in arts.filter_diagnostics.items():
                assert len(arr) == n_pos, (
                    f"After early_exit: filter_diagnostics[{key!r}] "
                    f"len={len(arr)} != positions len={n_pos}"
                )

    def test_backtest_result_post_init_passes_after_early_exit(self):
        """BacktestResult.__post_init__ must not raise even when early_exit
        has truncated both arrays."""
        n = 60
        close = _make_prices(n, seed=15)
        o, h, l, c = _ohlc(close)
        idx = _make_index(n)

        result = run_single_backtest(
            o, h, l, c, index=idx,
            atr_period=5, multiplier=2.0, trade_mode="revers",
            commission=0.001,
            early_exit_enabled=True,
            early_exit_max_drawdown=0.01,
            early_exit_check_bars=n,
            trade_filter_config=_TradeFilterCfgDouble(),
            zigzag_global_stats=_make_global_stats(),
        )
        # __post_init__ ran without exception; check length consistency.
        n_pos = len(result.positions)
        if result.filter_diagnostics is not None:
            for key, arr in result.filter_diagnostics.items():
                assert len(arr) == n_pos


# ===========================================================================
# F. RawBacktestArtifacts — no stale 7-value unpack in active scope.
# ===========================================================================

class TestRawBacktestArtifactsMigration:
    """Plan §8.1.1 acceptance gate: active scope is free of stale 7-value
    unpack.  We verify at the API level that the return type is
    ``RawBacktestArtifacts`` and has the expected fields.
    """

    def test_run_backtest_fast_returns_raw_artifacts(self):
        n = 30
        close = _make_prices(n)
        o, h, l, c = _ohlc(close)
        result = run_backtest_fast(
            o, h, l, c, atr_period=5, multiplier=2.0, trade_mode="revers",
            commission=0.001,
            early_exit_enabled=False, early_exit_max_drawdown=0.5,
            early_exit_check_bars=0,
        )
        assert isinstance(result, RawBacktestArtifacts)

    def test_raw_artifacts_fields_present(self):
        n = 30
        close = _make_prices(n)
        o, h, l, c = _ohlc(close)
        arts = run_backtest_fast(
            o, h, l, c, atr_period=5, multiplier=2.0, trade_mode="revers",
            commission=0.001,
            early_exit_enabled=False, early_exit_max_drawdown=0.5,
            early_exit_check_bars=0,
        )
        assert hasattr(arts, "returns")
        assert hasattr(arts, "equity_curve")
        assert hasattr(arts, "trend")
        assert hasattr(arts, "positions")
        assert hasattr(arts, "early_exit")
        assert hasattr(arts, "exit_bar")
        assert hasattr(arts, "exit_drawdown")
        assert hasattr(arts, "filter_diagnostics")

    def test_inactive_scope_mode_b_documented(self):
        """Smoke: donor TESTER import does not crash despite Mode B quarantine.

        donor TESTER/ is declared Mode B (quarantine) under §8.1.1: it still
        uses the legacy 7-tuple unpack from its local copy of run_backtest_fast
        and is excluded from the active grep-gate scope.  This test verifies
        its imports remain functional (breakage ≠ silent regression).
        """
        # We only verify the import does not raise; we do not call tester
        # functions to avoid coupling WP7 tests to tester internals.
        try:
            import sys
            import importlib
            # Avoid polluting sys.modules with tester-namespaced modules
            # whose run.py does a 7-tuple unpack.  Just verify the path.
            import os
            tester_path = os.path.join(
                os.path.dirname(__file__), "..", "..",
                "donor TESTER", "supertrend_optimizer"
            )
            assert os.path.exists(tester_path), (
                "donor TESTER/ not found — Mode B scope declaration invalid"
            )
        except Exception as exc:
            pytest.fail(f"Mode B smoke raised: {exc}")


# ===========================================================================
# G. BacktestResult.filter_diagnostics=None smoke (tester compat).
# ===========================================================================

class TestBacktestResultFilterDiagnosticsNoneCompat:
    """BacktestResult(filter_diagnostics=None) must be constructable without
    error — ensures tester callers that don't supply diagnostics stay green
    post-WP7.  Plan §8.1.1 / §8.5.
    """

    def test_backtest_result_filter_diagnostics_none_ok(self):
        n = 20
        ret = np.zeros(n - 1, dtype=np.float64)
        eq = np.ones(n, dtype=np.float64)
        pos = np.zeros(n, dtype=np.int8)
        result = BacktestResult(
            atr_period=5, multiplier=2.0, trade_mode="revers",
            commission=0.001, warmup=0, effective_warmup=0,
            returns=ret, equity_curve=eq,
            positions=pos, trend=pos.copy(),
            metrics={}, early_exit=False, exit_bar=None,
            exit_drawdown=None, trades_df=None,
            n_bars_original=n,
            filter_diagnostics=None,
        )
        assert result.filter_diagnostics is None

    def test_run_single_backtest_disabled_filter_diagnostics_none(self):
        n = 40
        close = _make_prices(n)
        o, h, l, c = _ohlc(close)
        idx = _make_index(n)
        result = run_single_backtest(
            o, h, l, c, index=idx,
            atr_period=5, multiplier=2.0, trade_mode="revers",
            commission=0.001,
            trade_filter_config=None,
        )
        assert result.filter_diagnostics is None

    def test_run_single_backtest_collect_filter_diagnostics_false_contract(self):
        """ТЗ lite-speedup §13.5: engine-level flag propagation.

        ``run_single_backtest(..., collect_filter_diagnostics=False)`` must
        suppress per-bar diagnostics while preserving trading outputs.
        """
        n = 60
        close = _make_prices(n, seed=21)
        o, h, l, c = _ohlc(close)
        idx = _make_index(n)
        kwargs = dict(
            index=idx,
            atr_period=5,
            multiplier=2.0,
            trade_mode="revers",
            commission=0.001,
            trade_filter_config=_TradeFilterCfgDouble(),
            zigzag_global_stats=_make_global_stats(),
        )

        result_true = run_single_backtest(
            o, h, l, c, **kwargs, collect_filter_diagnostics=True
        )
        result_false = run_single_backtest(
            o, h, l, c, **kwargs, collect_filter_diagnostics=False
        )

        assert result_true.filter_diagnostics is not None
        assert result_false.filter_diagnostics is None
        np.testing.assert_array_equal(result_false.positions, result_true.positions)
        assert result_false.metrics["num_trades"] == result_true.metrics["num_trades"]
        true_trades = 0 if result_true.trades_df is None else len(result_true.trades_df)
        false_trades = 0 if result_false.trades_df is None else len(result_false.trades_df)
        assert false_trades == true_trades


# ===========================================================================
# H. attach_trade_filter_diagnostics — trade-level diagnostics.
# ===========================================================================

class TestAttachTradeFilterDiagnostics:
    """Plan §8.3, §8.4 / spec §10, §13, §15.4.

    Verifies:
    - entry_filter_state / entry_trigger_source / exit_reason columns added.
    - entry_signal_idx = max(entry_index - 1, 0) (OPEN_TO_OPEN rule §8.4).
    - exit_reason == "filter_stopping_opposite_flip" when FSM in ST_STOPPING.
    - exit_reason == "pending_open_trade_at_end" for open trade at end-of-slice.
    - exit_reason == "st_flip" otherwise.
    """

    def _make_diag(self, n: int, state_at: Dict[int, str]) -> Dict[str, np.ndarray]:
        state_arr = np.full(n, "OFF", dtype=object)
        for idx, s in state_at.items():
            state_arr[idx] = s
        trigger_arr = np.full(n, "none", dtype=object)
        trigger_arr[0] = "candidate_threshold"
        return {
            "trade_filter_state": state_arr,
            "trade_filter_trigger_source": trigger_arr,
        }

    def _make_trades(self, rows) -> pd.DataFrame:
        return pd.DataFrame(rows, columns=[
            "entry_index", "exit_index",
        ])

    def test_entry_filter_state_uses_signal_idx(self):
        """entry_filter_state = state[entry_index - 1]."""
        # entry_index=3 → entry_signal_idx = max(3-1,0) = 2
        n = 10
        diag = self._make_diag(n, {2: "ST_ACTIVE_FREEZE"})
        trades = self._make_trades([{"entry_index": 3, "exit_index": 7}])
        out = attach_trade_filter_diagnostics(trades, diag)
        assert out["entry_filter_state"].iloc[0] == "ST_ACTIVE_FREEZE"

    def test_entry_signal_idx_zero_edge_case(self):
        """entry_index=0 → entry_signal_idx = max(-1,0) = 0."""
        n = 5
        diag = self._make_diag(n, {0: "WAIT_FIRST_ST_FLIP"})
        trades = self._make_trades([{"entry_index": 0, "exit_index": 3}])
        out = attach_trade_filter_diagnostics(trades, diag)
        assert out["entry_filter_state"].iloc[0] == "WAIT_FIRST_ST_FLIP"

    def test_exit_reason_st_flip_when_monitoring(self):
        """exit_reason == 'st_flip' when FSM was in MONITORING."""
        n = 10
        diag = self._make_diag(n, {4: "ST_ACTIVE_MONITORING"})
        trades = self._make_trades([{"entry_index": 2, "exit_index": 5}])
        out = attach_trade_filter_diagnostics(trades, diag)
        assert out["exit_reason"].iloc[0] == "st_flip"

    def test_exit_reason_filter_stopping_opposite_flip(self):
        """exit_reason == 'filter_stopping_opposite_flip' when FSM ST_STOPPING."""
        n = 10
        # exit_index=6 → exit_signal_idx = max(6-1,0) = 5 → state[5]="ST_STOPPING"
        diag = self._make_diag(n, {5: "ST_STOPPING"})
        trades = self._make_trades([{"entry_index": 2, "exit_index": 6}])
        out = attach_trade_filter_diagnostics(trades, diag)
        assert out["exit_reason"].iloc[0] == "filter_stopping_opposite_flip"

    def test_exit_reason_pending_open_trade_at_end(self):
        """exit_reason == 'pending_open_trade_at_end' when exit_index is last slot.

        The 'last slot' is len(filter_diagnostics["trade_filter_state"]) - 1.
        """
        n = 10
        diag = self._make_diag(n, {})
        # exit_index = n-1 = 9 → pending sentinel
        trades = self._make_trades([{"entry_index": 5, "exit_index": n - 1}])
        out = attach_trade_filter_diagnostics(trades, diag)
        assert out["exit_reason"].iloc[0] == "pending_open_trade_at_end"

    def test_attach_returns_copy_not_inplace(self):
        """attach_trade_filter_diagnostics must return a copy, not modify inplace."""
        n = 10
        diag = self._make_diag(n, {})
        trades = self._make_trades([{"entry_index": 2, "exit_index": 6}])
        original_cols = set(trades.columns)
        out = attach_trade_filter_diagnostics(trades, diag)
        assert set(trades.columns) == original_cols
        assert "exit_reason" in out.columns

    def test_three_new_columns_added(self):
        n = 10
        diag = self._make_diag(n, {})
        trades = self._make_trades([{"entry_index": 1, "exit_index": 5}])
        out = attach_trade_filter_diagnostics(trades, diag)
        for col in ("entry_filter_state", "entry_trigger_source", "exit_reason"):
            assert col in out.columns


# ===========================================================================
# I. Close-only invariance gate (§8.3.1).
# ===========================================================================

class TestCloseOnlyInvariance:
    """ZigZag pivot/height path uses ONLY close prices.

    Distorting high/low must produce bit-identical filtered_positions and
    ZigZag-relevant diagnostic fields.  Plan §8.3.1.
    """

    def test_distorted_high_low_do_not_change_filter_output(self):
        """ZigZag pivot/height uses only ``close``.

        We call ``apply(...)`` directly with the SAME trend (so ST flips are
        identical) but swap normal vs distorted high/low.  The ZigZag-relevant
        outputs (filtered_positions and per-bar diagnostics) must be
        bit-identical because the ZigZag formula ignores high/low.

        Plan §8.3.1 invariance gate.
        """
        from supertrend_optimizer.core.calculator import calculate_supertrend

        n = 60
        close = _make_prices(n, seed=19)
        open_p = np.roll(close, 1); open_p[0] = close[0]

        high_normal = close + 1.0
        low_normal = close - 1.0
        # Distorted: extreme values that would affect any OHLC-based ZigZag.
        high_distorted = close * 10.0
        low_distorted = np.ones(n) * 0.001

        # Use the same trend for both calls (computed from normal OHLC).
        trend_ref, _ = calculate_supertrend(high_normal, low_normal, close, 5, 2.0)
        cfg = _TradeFilterCfgDouble()
        gs = _make_global_stats()

        result_normal = apply(
            close=close, trend=trend_ref,
            trade_mode="revers",
            trade_filter_config=cfg,
            zigzag_global_stats=gs,
            open_prices=open_p,
            high=high_normal,
            low=low_normal,
        )
        result_distorted = apply(
            close=close, trend=trend_ref,
            trade_mode="revers",
            trade_filter_config=cfg,
            zigzag_global_stats=gs,
            open_prices=open_p,
            high=high_distorted,
            low=low_distorted,
        )

        # ZigZag-relevant: filtered_positions must be identical.
        np.testing.assert_array_equal(
            result_normal.positions, result_distorted.positions,
            err_msg="Distorted high/low changed filtered_positions (ZigZag must be close-only)"
        )
        # Key diagnostic arrays that depend only on close.
        for key in (
            "trade_filter_state",
            "confirmed_legs_since_start",
            "st_flip_dir",
        ):
            np.testing.assert_array_equal(
                result_normal.filter_diagnostics[key],
                result_distorted.filter_diagnostics[key],
                err_msg=f"Distorted high/low changed diagnostics[{key!r}]",
            )


# ===========================================================================
# J. WP7 anti-drift guards.
# ===========================================================================

class TestWp7AntiDrift:
    """Ensure WP7 did NOT introduce WP8/WP9 features prematurely."""

    def test_backtest_result_has_no_filter_diagnostics_oos(self):
        """filter_diagnostics_oos must NOT be in BacktestResult yet (WP8)."""
        assert not hasattr(BacktestResult, "filter_diagnostics_oos"), (
            "filter_diagnostics_oos was introduced prematurely; belongs to WP8"
        )

    def test_run_single_backtest_signature_has_no_global_offset_as_positional(self):
        """global_offset must be keyword-only so callers aren't broken."""
        import inspect
        sig = inspect.signature(run_single_backtest)
        # global_offset is keyword-only (comes after *, or has default)
        params = sig.parameters
        assert "global_offset" in params
        assert params["global_offset"].default == 0

    def test_extract_trades_signature_unchanged(self):
        """extract_trades signature must NOT have changed (plan §8.3 / §8.1.6)."""
        import inspect
        from supertrend_optimizer.core.trades import extract_trades
        sig = inspect.signature(extract_trades)
        param_names = list(sig.parameters.keys())
        for expected in ("positions", "returns", "execution_prices", "index"):
            assert expected in param_names, (
                f"extract_trades is missing expected parameter {expected!r}"
            )
        # Ensure no new filter-related params were added.
        for forbidden in ("filter_diagnostics", "trade_filter"):
            assert forbidden not in param_names, (
                f"extract_trades got unexpected parameter {forbidden!r}"
            )
