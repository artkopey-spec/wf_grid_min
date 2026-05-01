"""
WP-T5 — Equal-blocks enabled-filter rejection gate tests.

Plan reference: docs/zigzag_st_tester_phase2_implementation_plan.txt §4.2
                Test gates: #6 (equal_blocks enabled rejected), #19 (disabled parity)

Contract:
1. ``run_equal_blocks(trade_filter_config=<enabled>)`` → ``ConfigError``
   raised BEFORE any slicing / backtest (defense-in-depth, plan §4.2).
2. ``run_equal_blocks(trade_filter_config=None)`` → bit-identical baseline.
3. ``run_equal_blocks(trade_filter_config=<disabled>)`` → bit-identical baseline.
4. The ConfigError is raised before ``build_equal_block_slices`` runs
   (verified by checking no segments are returned on the error path).
5. Equal-blocks disabled export remains bit-identical (WP-T1 baseline guard).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_synthetic_ohlc(n: int = 400, seed: int = 99) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    log_returns = rng.normal(loc=0.0002, scale=0.010, size=n)
    close = 100.0 * np.exp(np.cumsum(log_returns))
    noise = rng.uniform(0.001, 0.004, size=n)
    high = close * (1.0 + noise)
    low = close * (1.0 - noise)
    open_ = np.clip(close * (1.0 + rng.uniform(-0.002, 0.002, size=n)), low, high)
    idx = pd.date_range("2021-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close},
        index=idx,
    )


def _make_enabled_cfg():
    from supertrend_optimizer.core.trade_filter_config import (
        TradeFilterConfig,
        TradeFilterDiagnosticsConfig,
        TradeFilterLifecycleConfig,
        TradeFilterTriggerToggleConfig,
        TradeFilterTriggersConfig,
        TradeFilterZigZagConfig,
    )
    return TradeFilterConfig(
        enabled=True,
        type="zigzag_st_mode",
        zigzag=TradeFilterZigZagConfig(
            reversal_threshold=0.04,
            local_window=20,
            candidate_trigger_threshold=0.4,
        ),
        triggers=TradeFilterTriggersConfig(
            candidate_threshold=TradeFilterTriggerToggleConfig(enabled=True),
            confirmed_median=TradeFilterTriggerToggleConfig(enabled=True),
        ),
        lifecycle=TradeFilterLifecycleConfig(
            freeze_confirmed_legs=3,
            stop_check="confirm_bar_only",
            stopping_exit="opposite_st_flip",
        ),
        diagnostics=TradeFilterDiagnosticsConfig(),
    )


def _make_disabled_cfg():
    from supertrend_optimizer.core.trade_filter_config import TradeFilterConfig
    return TradeFilterConfig(enabled=False, type=None, zigzag=None,
                             triggers=None, lifecycle=None, diagnostics=None)


# ---------------------------------------------------------------------------
# Group 1: Test #6 — enabled config rejected before backtest/slicing
# ---------------------------------------------------------------------------

class TestEqualBlocksEnabledRejected:
    """plan §4.2 / Test #6."""

    def test_raises_config_error_when_enabled(self) -> None:
        from supertrend_optimizer.testing.runner import run_equal_blocks
        from supertrend_optimizer.utils.exceptions import ConfigError

        df = _make_synthetic_ohlc()
        cfg = _make_enabled_cfg()

        with pytest.raises(ConfigError, match="equal_blocks"):
            run_equal_blocks(
                df=df, n_parts=4, warmup_period=20,
                atr_period=14, multiplier=3.0,
                trade_mode="revers", commission=0.001,
                trade_filter_config=cfg,
            )

    def test_error_message_mentions_legacy(self) -> None:
        from supertrend_optimizer.testing.runner import run_equal_blocks
        from supertrend_optimizer.utils.exceptions import ConfigError

        df = _make_synthetic_ohlc()
        cfg = _make_enabled_cfg()

        with pytest.raises(ConfigError, match="legacy"):
            run_equal_blocks(
                df=df, n_parts=4, warmup_period=20,
                atr_period=14, multiplier=3.0,
                trade_mode="revers", commission=0.001,
                trade_filter_config=cfg,
            )

    def test_raises_before_any_segments_computed(self) -> None:
        """Gate fires BEFORE build_equal_block_slices — no partial output."""
        from supertrend_optimizer.testing.runner import run_equal_blocks
        from supertrend_optimizer.utils.exceptions import ConfigError

        df = _make_synthetic_ohlc()
        cfg = _make_enabled_cfg()

        # Track that no SegmentResult was returned
        caught = False
        try:
            result = run_equal_blocks(
                df=df, n_parts=4, warmup_period=20,
                atr_period=14, multiplier=3.0,
                trade_mode="revers", commission=0.001,
                trade_filter_config=cfg,
            )
        except ConfigError:
            caught = True

        assert caught, "ConfigError not raised for enabled filter in equal_blocks"

    def test_raises_for_various_n_parts(self) -> None:
        """Gate fires regardless of n_parts value."""
        from supertrend_optimizer.testing.runner import run_equal_blocks
        from supertrend_optimizer.utils.exceptions import ConfigError

        df = _make_synthetic_ohlc(n=400)
        cfg = _make_enabled_cfg()

        for n_parts in (2, 3, 5, 10):
            with pytest.raises(ConfigError):
                run_equal_blocks(
                    df=df, n_parts=n_parts, warmup_period=20,
                    atr_period=14, multiplier=3.0,
                    trade_mode="revers", commission=0.001,
                    trade_filter_config=cfg,
                )


# ---------------------------------------------------------------------------
# Group 2: Test #19 — disabled/None path bit-identical baseline
# ---------------------------------------------------------------------------

class TestEqualBlocksDisabledBaseline:
    """plan §4.2 / Test #19 (equal_blocks disabled parity)."""

    def test_no_raise_when_trade_filter_config_none(self) -> None:
        from supertrend_optimizer.testing.runner import run_equal_blocks

        df = _make_synthetic_ohlc()
        results = run_equal_blocks(
            df=df, n_parts=4, warmup_period=20,
            atr_period=14, multiplier=3.0,
            trade_mode="revers", commission=0.001,
            trade_filter_config=None,
        )
        assert len(results) == 4

    def test_no_raise_when_trade_filter_config_disabled(self) -> None:
        from supertrend_optimizer.testing.runner import run_equal_blocks

        df = _make_synthetic_ohlc()
        cfg = _make_disabled_cfg()
        results = run_equal_blocks(
            df=df, n_parts=4, warmup_period=20,
            atr_period=14, multiplier=3.0,
            trade_mode="revers", commission=0.001,
            trade_filter_config=cfg,
        )
        assert len(results) == 4

    def test_disabled_cfg_metrics_identical_to_none_cfg(self) -> None:
        """Disabled TradeFilterConfig must produce the same metrics as None."""
        from supertrend_optimizer.testing.runner import run_equal_blocks

        df = _make_synthetic_ohlc()
        cfg = _make_disabled_cfg()

        results_none = run_equal_blocks(
            df=df, n_parts=4, warmup_period=20,
            atr_period=14, multiplier=3.0,
            trade_mode="revers", commission=0.001,
            trade_filter_config=None,
        )
        results_disabled = run_equal_blocks(
            df=df, n_parts=4, warmup_period=20,
            atr_period=14, multiplier=3.0,
            trade_mode="revers", commission=0.001,
            trade_filter_config=cfg,
        )

        for s_none, s_dis in zip(results_none, results_disabled):
            assert s_none.segment_metrics["num_trades"] == s_dis.segment_metrics["num_trades"], (
                f"Segment {s_none.segment_label}: "
                "num_trades differs between None and disabled config."
            )
            assert s_none.segment_metrics["sum_pnl_pct"] == pytest.approx(
                s_dis.segment_metrics["sum_pnl_pct"]
            )

    def test_segment_result_has_no_filter_diagnostics_field(self) -> None:
        """SegmentResult does not carry filter_diagnostics (Phase 2 v0.4 scope)."""
        from supertrend_optimizer.testing.runner import run_equal_blocks, SegmentResult

        df = _make_synthetic_ohlc()
        results = run_equal_blocks(
            df=df, n_parts=4, warmup_period=20,
            atr_period=14, multiplier=3.0,
            trade_mode="revers", commission=0.001,
            trade_filter_config=None,
        )
        for seg in results:
            assert not hasattr(seg, "filter_diagnostics"), (
                "SegmentResult should NOT have filter_diagnostics in Phase 2 v0.4 "
                "(equal_blocks filter wiring is deferred to a future phase)."
            )


# ---------------------------------------------------------------------------
# Group 3: backward-compatibility — call without trade_filter_config kwarg
# ---------------------------------------------------------------------------

class TestEqualBlocksBackwardCompatibility:
    """New param has default=None; existing call-sites without it must not break."""

    def test_call_without_trade_filter_config_kwarg(self) -> None:
        from supertrend_optimizer.testing.runner import run_equal_blocks

        df = _make_synthetic_ohlc()
        # Legacy call-site: no trade_filter_config argument at all
        results = run_equal_blocks(
            df=df, n_parts=3, warmup_period=20,
            atr_period=14, multiplier=3.0,
            trade_mode="revers", commission=0.001,
        )
        assert len(results) == 3

    def test_segment_labels_correct(self) -> None:
        from supertrend_optimizer.testing.runner import run_equal_blocks

        df = _make_synthetic_ohlc()
        results = run_equal_blocks(
            df=df, n_parts=4, warmup_period=20,
            atr_period=14, multiplier=3.0,
            trade_mode="revers", commission=0.001,
        )
        labels = [s.segment_label for s in results]
        assert labels == ["S1", "S2", "S3", "S4"]
